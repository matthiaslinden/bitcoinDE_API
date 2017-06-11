###############################################################################
#
# The MIT License (MIT)
#
# Copyright (c) 2016 Matthias Linden
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
###############################################################################

#!/usr/bin/env python2.7
#coding:utf-8

from json import loads
import time
from hashlib import md5,sha256
from hmac import new as hmac_new

# Building upon twisted 
from zope.interface import implements
from twisted.web.iweb import IBodyProducer
from twisted.internet.defer import Deferred,succeed
from twisted.web.client import Agent,readBody,WebClientContextFactory,HTTPConnectionPool
from twisted.web.http_headers import Headers
from twisted.internet.protocol import Protocol	

# How it works:
# 
# - BitcoinDeAPI get's requests via APIRequest(call,**kwargs) and checks valdity of call and arguments (some) 
#    The Request is then encoded in the bitcoin.de-API-complient format and handled over to a request-agent (twisted.web.client.Agent)
#    The agent's deferred is returned, The whole class lays a base for a request-queue (with priority)
#    The nonce is handled here as well as revocery from nonce-error
# 
# - BitcoinDeAPINonce improves the nonce-error handling in case of back-to-back requests which might arrive out of order
# - QueuedBitcoinDeAPI implements a request-queue, delaying calls if no credits are available
#    in addition the same requests (call,params hash) aren't added multiple times to the queue,
#    but the original request's deferred is shared with the new request (hiding abstraction from the user)
# - PriorityBitcoinDeAPI orders the queue by a priority
#
# - BtcdeAPIProtocol is used in 'response.deliverBody(...)' to parse the request's response
# - StringProducer handles body-generation for POST-Requests

# TODO 03.06.2017: Track Down error handling for Protocol JSON error

class BitcoinDeAPI(object):
	def __init__(self,reactor,api_key,api_secret):
		# Bitcoin.de API URI
		apihost = 'https://api.bitcoin.de'
		apiversion = 'v1'
		orderuri = apihost + '/' + apiversion + '/' + 'orders'
		tradeuri = apihost + '/' + apiversion + '/' + 'trades'
		accounturi = apihost + '/' + apiversion + '/' + 'account'
		# set initial nonce
		self.nonce = int(time.time())
		
		self.reactor = reactor
		pool = HTTPConnectionPool(reactor)		# Actually reusing the connection leads to correct credits
		pool.maxPersistentPerHost = 1
		self.contextFactory = WebClientContextFactory()
		self.agent = Agent(self.reactor, self.contextFactory,pool=pool)
		
		self.api_key = api_key
		self.api_secret = api_secret
		
		self.calls = {}
		# Method,uri,required params with allowed values,credits,field to return (after credits/pages are stripped)
		# Orders
		self.calls['showOrderbook'] = ['GET',orderuri,{'type':['sell','buy']},2]
		self.calls['showOrderbookCompact'] = ['GET',orderuri+'/compact',{},3]
		self.calls['createOrder'] = ['POST',orderuri,{'type':['sell','buy'],'max_amount':[],'price':[]},1]
		self.calls['deleteOrder'] = ['DELETE',orderuri,{'order_id':[]},2]
		self.calls['showMyOrders'] = ['GET',orderuri+'/my_own',{},2]	# Fix: all arguments are optional
		self.calls['showMyOrderDetails'] = ['GET',orderuri,{'order_id':[]},2]
		# Trades
		self.calls['executeTrade'] = ['POST',tradeuri,{'order_id':[],'amount':[]},1]
		self.calls['showMyTradeDetails'] = ['GET',tradeuri,{'trade_id':[]},3]
		self.calls['showMyTrades'] = ['GET',tradeuri,{},3]
		self.calls['showPublicTradeHistory'] = ['GET',tradeuri+'/history',{'since_tid':[]},3]
		# Account
		self.calls['showAccountInfo'] = ['GET',accounturi,{},2]
		self.calls['showAccountLedger'] = ['GET',accounturi+'/ledger',{},3]
		# Other
		self.calls['showRates'] = ['GET',apihost+'/'+apiversion+'/rates',{},3]
		
	
	def APIRequest(self,call,**kwargs):
		"""Compiles the Request, checks parameters and sets uri,method and credits"""
		if call in self.calls.keys():
			method,uri,required,credits = self.calls[call]
			for k,req in required.items():
				if len(req) != 0:	# No restriciton on values
					if kwargs.get(k,None) not in req:
						print ""
						return None
				else:
					if k not in kwargs.keys():
						return None
			if 'order_id' in required.keys():	# Add OrderID if it's a required field
				uri += '/'+kwargs["order_id"]
				if method == 'GET':
					kwargs = {}
			if 'trade_id' in required.keys():
				uri += '/'+kwargs["trade_id"]
				if method == 'GET':
					kwargs = {}
			priority = kwargs.get('priority',1)
			if 'priority' in kwargs.keys():
				del kwargs["priority"]
			return self.EnqueAPIRequest(method,kwargs,uri,credits,priority)
		else:
			# Unknown request
			d = Deferred()
			d.errback(["Unknown request"])
			return d
	
	def EnqueAPIRequest(self,method,params,uri,credits,priority):
		return self.APIConnect(method,params,uri)
		
	def APIConnect(self,method,params,uri,eid=None):
		"""Encapsulates all the API encoding, starts the HTTP request, returns deferred
			eid is used to pass Data along the chain to be used later
		"""
		encoded_string = ''
		if params:
			for key, value in sorted(params.iteritems(),key=lambda (key,value,) : key ):
				encoded_string += str(key) + '=' + str(value) + '&'
			encoded_string = encoded_string[:-1]
			url = uri + '?' + encoded_string
		else:
			url = uri
		self.nonce += 1
		
		if method == 'POST':
			md5_encoded_query_string = md5(encoded_string).hexdigest()
		else:
			md5_encoded_query_string = md5('').hexdigest()
		
		hmac_data = method + '#' + url + '#' + self.api_key + '#' + str(self.nonce) + '#' + md5_encoded_query_string
		hmac_signed = hmac_new(self.api_secret,digestmod=sha256, msg=hmac_data).hexdigest()
			
		header = {'content-type':['application/x-www-form-urlencoded;charset=utf-8']}
		header[b"X-API-KEY"] = [self.api_key]
		header[b"X-API-NONCE"] = [b"%d"%self.nonce]
		header[b"X-API-SIGNATURE"] =  [hmac_signed]
	
		h = Headers({})
		for k,v in header.items():
			h.setRawHeaders(k,v)
		
		bodyProducer = None
		if method == 'POST':
			bodyProducer = StringProducer(encoded_string)
			
		d = self.agent.request(method,url,headers=h,bodyProducer=bodyProducer)
		d.addCallback(self.APIResponse,eid=eid)
		
		def Error(result):
			print "API-Connect-Error",result
		d.addErrback(Error)
		return d
	
	def APIResponse(self,response,eid):
		"""Process Response.header and choose treatment of the body"""
		finished = Deferred()
		response.deliverBody(BtcdeAPIProtocol(finished))
		header = {"code":response.code,"phrase":response.phrase}
		
		if response.code == 200 or response.code == 201:
			finished.addCallback(self.DequeueAPIRequest,eid=eid,header=header)
		elif response.code == 429 or response.code == 403:
			retry = int(response.headers.getRawHeaders("Retry-After")[0])
			header["retry"] = retry
			finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
		else:
			print response.code
			finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
			
		def Error(result):
			print "APIResponse DeferredError after code %d"%(response.code)
		finished.addErrback(Error)
		
		return finished
		
	def DequeueAPIRequest(self,response,eid,header):
		"""Append header fields (code,phrase) to the actual data"""
		r = response
		r.update(header)
		self.HandleAPISuccess(header)
		return r
		
	def DequeueAPIErrors(self,response,eid,header):
		"""Pick error info (message,code) from Response.body and return it along with the header (code,phrase,[retry])"""
		try:
			errors = response.get("errors",[{}])[0]
		except:
			print "Unknown Error"
			header["message"] = "Unknown error"
			return header
		else:
			header["errmessage"] = errors.get("message","")
			header["errcode"] = errors.get("code",-1)
			print header
			self.HandleAPIError(header)
			return header
	
	def HandleAPIError(self,header):
		pass
		
	def HandleAPISuccess(self,header):
		pass
			
	def ResetNonce(self):
		"""Reset the nonce to the 'default' value, which is just the current unix-time and should suffice as at most 0.5Hz query frequency are sustainable"""
		self.nonce = int(time.time())

class BitcoinDeAPINonce(BitcoinDeAPI):
	"""Adds Nonce Error Handling."""
	def __init__(self,reactor,api_key,api_secret):
		super(BitcoinDeAPINonce,self).__init__(reactor,api_key,api_secret)
		
		self.successful_nonce = 0	# Counts successfull Nonces
		
	def HandleAPIError(self,header):
		code,message = header["errcode"],header["errmessage"]
		if code == 4:	# Invalid Nonce
			self.InvalidNonce()
			
	def HandleAPISuccess(self,header):
		self.SuccessfulNonce()
	
	def SuccessfulNonce(self):
		""" Count up to 5 successive valid nonces"""
		self.successful_nonce = max(min(4,self.successful_nonce+1),0)
		
	def InvalidNonce(self):
		"""Decrease by one for every invalid nonce, if negative: reset"""
		self.successful_nonce -= 1
		if self.successful_nonce < 0:
			self.ResetNonce()

class QueuedAPIRequest(object):
	"""Queued API Request to be stored till it's processed"""
	def __init__(self,eid,rhash,method,uri,params,credits,deferred,priority):
		self.eid = eid
		self.rhash = rhash
		self.method=method
		self.uri=uri
		self.params=params
		self.credits = credits
		# List of deferreds which are waiting for the result --> DeliverResult
		self.deferreds = [deferred]
		self.done = 0
		self.attempts = 0
		self.priority = priority
		
	def Send(self):
		self.attempts += 1
		
	def AddDeferred(self,deferred):
		self.deferreds.append(deferred)
		
	def DeliverResult(self,result):
		result["attempts"] = self.attempts
		for d in self.deferreds:
			d.callback(result)
		self.done = 1
		
	
class QueuedBitcoinDeAPI(BitcoinDeAPINonce):
	"""Implements a Queue that holds requests and manages credits"""
	def __init__(self,reactor,api_key,api_secret):
		super(QueuedBitcoinDeAPI,self).__init__(reactor,api_key,api_secret)
		
		self.requestID = 0
		self.queue = {}
		self.pending = {}
		# Store the Reshedule Handle
		self.retrycall = self.reactor.callLater(.1,self.IssueNext)	# Dummy call, delayed start at .1
		
		# Credits
		self.wait_for_credits = 0
		self.lasttime = time.time()
		self.lastcredits = 5
		self.retryperiode = 3
		self.max_seen = 8
		
		self.credits_spent = 0
		
		# Hack to make CalcHash create unique values on demand
		self.unique_salt = 0
	
	def CalcHash(self,method,uri,params):
		"""if unique=True is passed as param, an additional salt is added	"""
		h = 0
		for p in params.values():
			h += hash(p)
		unique = params.pop("unique",False)
		if unique == True:
			self.unique_salt = (self.unique_salt+59999)%60013
			h += self.unique_salt
		ha = hash(method)+hash(uri)+h
		return ha
		
	def SameHashInQueue(self,h):
		"""Return an already queued Request if it has similar hash to the requested one """
		rk,rreq = -1,None
		for k,req in self.queue.items():
			if req.rhash == h:
				rk,rreq = k,req
				break
		return rk,rreq
		
	def Queue(self):
		return self.queue.items()
		
	def IssueNext(self):
	#	print "IssueNext",len(self.queue),len(self.pending)
		self.wait_for_credits = 0
#		dt = 0.32	# Explicit pause inbetween two back to back request to avoid bad nonces
#		dt = 0.54
		dt = 0.4
		if self.EnoughCreditsAvailable(3):
			for k,req in self.Queue():
				if k not in self.pending.keys():
					if req.attempts < 10:
						self.APIConnect(req.method,req.params,req.uri,k) # Chaining of APIResponse is done in this function, so returned deferred is not used.
						self.pending[k] = req.credits
						self.credits_spent += req.credits
						req.Send()
					else:
						req.DeliverResult({"error":"too many unsuccesful attempts","attempts":req.attempts})
						self.DeleteRequest(k)
					break
		else:
			dt = 2
		if len(self.queue) > len(self.pending):	# (double counting in queue and pending)
			# Schedule Next Issue, either back to back or when enough credits should be available
			self.ScheduleNextIssue(dt)
		
	def ScheduleNextIssue(self,dt=None):
		"""Is called from IssueNext, APIRequest and whenever Timing has to be updated (due to tight credits) """
		if dt== None:
			dt = 0.0
		if dt > 2:
			self.lastcredits = -dt-1
			self.lasttime = time.time()
			if self.wait_for_credits == 1 or self.retrycall.active():
				self.retrycall.reset(dt)
			else:
				self.retrycall = self.reactor.callLater(dt,self.IssueNext)
		else:
			if self.wait_for_credits == 0:
				if self.retrycall.active():
					self.retrycall.reset(dt)
				else:
					self.retrycall = self.reactor.callLater(dt,self.IssueNext)
				
	def Reenqueue(self,eid):
		if eid in self.pending.keys():
			del self.pending[eid]
		self.ScheduleNextIssue()
					
	def DeleteRequest(self,eid):
		del self.queue[eid]
		if eid in self.pending.keys():
			del self.pending[eid]
	
	def CreditsAvailable(self):
		ct = time.time()
		dt = ct-self.lasttime
		return min(self.max_seen,self.lastcredits+dt)-sum(self.pending.values())
	
	def EnoughCreditsAvailable(self,credits):
		available = self.CreditsAvailable()
	#	print "inflight:",sum(self.pending.values()),"credits",available
		if available > 2+credits:
			return True
		else:
			return False
	
	def QueueCreditsAvailable(self):
		""" Returns a value reflecting the number of credits available with regard to enqueued requests"""
		queuecredits = [x.credits for x in self.queue.values()]
		return max(len(self.queue),self.CreditsAvailable()-sum(queuecredits))
	
	def EnqueAPIRequest(self,method,params,uri,credits,priority):
		finished = Deferred()
		
		h = self.CalcHash(method,uri,params)
		samereqID,samereq = self.SameHashInQueue(h)	# Return same request if already enqueued or pending
		if samereqID == -1:	# unique request
			eid = self.requestID
			self.requestID += 1
			request = QueuedAPIRequest(eid,h,method,uri,params,credits,finished,priority)	# Create the Request-object
			self.queue[eid] = request
			
		else:	# Request is already running
			eid = samereqID
			samereq.AddDeferred(finished)	# Add deferred to list of data-recipients
		
		self.ScheduleNextIssue()
		return finished
		
	def APIResponse(self,response,eid):
		"""Process Response.header and choose treatment of the body"""
		finished = Deferred()
		
		def Error(result):
			print "DeferredError after code %d"%(response.code)
		finished.addErrback(Error)
		
		response.deliverBody(BtcdeAPIProtocol(finished))
		req = self.queue[eid]
		header = {"code":response.code,"phrase":response.phrase,"call":req.method+":"+req.uri,"reqID":eid}
		if response.code == 200 or response.code == 201:
			finished.addCallback(self.DequeueAPIRequest,eid=eid,header=header)
			
		else:
			self.lastcredits -= self.pending[eid]
			if response.code == 429 or response.code == 403:	# 403 might need some extra handling
				if response.code == 403:
					print "\n"+header+"\n"
				retry = int(response.headers.getRawHeaders("Retry-After",[0])[0])
				header["retry"] = retry
				self.Reenqueue(eid)
				self.ScheduleNextIssue(retry+3)
				finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
				
			else:
				d = finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
				if response.code == 400:
					# TODO: response 400 can still have error codes like 27 (bad params, which should not lead to reenqueue)
					self.Reenqueue(eid)
					self.ScheduleNextIssue()
				else:
					# Every other error than 400 is not retried!
					d.addCallback(req.DeliverResult)	# Don't know if this is a problem, that req.DeliverResult is called, but the request is removed from the queue
					del self.pending[eid]
					del self.queue[eid]
					
					
				
		return finished
		
	def DequeueAPIRequest(self,response,eid,header):
		"""Handle (actual) credits,errors after Protocol has received all data"""
		req = self.queue[eid]
		response.update(header)
		response["attempts"] = req.attempts
		
		credits = response.get("credits",0)
		if credits > self.max_seen:
			self.max_seen = credits
		self.lasttime = time.time()
		self.lastcredits = credits
		
		self.queue[eid].DeliverResult(response)
		del self.pending[eid]
		del self.queue[eid]
		
		self.HandleAPISuccess(header)	# Handle success [successful_nonce counter]
		
		return response
		
	def HandleAPIError(self,header):
		code,message = header["errcode"],header["errmessage"]
		if code == 4:	# Invalid Nonce
			self.InvalidNonce()
		
	def APIRequestPages(self,call,pages,**kwargs):
		"""Request a certain number of pages
			- As Requests might take some time due to limited credits, pages are requested in blocks 
		"""
		pass
		
	def Status(self):
		return {"total_spent":self.credits_spent,"max":self.max_seen,"hot":self.QueueCreditsAvailable(),"avail":self.CreditsAvailable()}
		
class PriorityBitcoinDeAPI(QueuedBitcoinDeAPI):
	def Queue(self):
		q = sorted(self.queue.items(),key=lambda x : (-x[1].priority,x[1].eid))
		return q
	
class StringProducer(object):
	implements(IBodyProducer)
	"""Produces POST request bodies"""
	def __init__(self, body):
		self.body = body
		self.length = len(body)

	def startProducing(self, consumer):
		consumer.write(self.body)
		return succeed(None)

	def pauseProducing(self):
		pass

	def stopProducing(self):
		pass

class BtcdeAPIProtocol(Protocol):
	"""Processes the frames data, which might arrive in multiple packets, returns data when connection is finished"""
	def __init__(self,deferred):
		self.deferred = deferred
		self.partial = ""
	
	def dataReceived(self,data):
		self.partial += data
		
	def connectionLost(self,reason):
		try:
			if len(self.partial) > 0:
				data = loads(self.partial)	#json.loads
			else:
				print "API-Connection lost, but no data was received, didn't attempt JSON decode",reason
		except:
			print "JSON error",self.partial,reason
			self.deferred.errback(["JSON data couldn't be loaded properly",self.partial[-20:]])
		else:
			self.deferred.callback(data)

class MultipageFetchSession(object):
	"""
	a page_callback(items,progress) function can be passed.
Two Issues are adressed:
* Ensure that a complete dataset is fetched --> Constantly query page 1 and check for changes, if change occures, reissue the last burst.
* End after no new items occur --> ProcessPage-callback returns true if no more data is needed
"""
	_next_session = 0
	def __init__(self,api,cmd,page_callback=lambda x,y:False,complete=False,**kwargs):
		MultipageFetchSession._next_session += 1
		self.sessionID = MultipageFetchSession._next_session
		self.deferred = Deferred()
	#	self.deferred.addErrback(self.Errback,{"error":"global error","cmd":cmd,"sid":self.sessionID})

		# Basic config
		self.api = api
		self.cmd = cmd
		self.complete = complete	# Ensure completeness of data that is fetched by constantly checking for new data
		self.params = kwargs

		# derrived config
		self.bsize = int(max(3,(api.max_seen - 12)/3))	# Calculate number of sizes of the burst

		print "Session %d"%self.sessionID,self.cmd,self.params,"burstsize:",self.bsize

		# session-tracking
		self.pages_fetched = {} # page -> number of times fetched
		self.pages_pending = {}	# page -> deferred dict
		self.revert = False

		# Data Tracking
		self.items = {}

		# Per Page callback
		self.page_callback = page_callback	# per Page-results callback, if one returns True in a burst, finish fetching!
		self.burst_pages = {} # dict of page --> finished [true/false]

		# Start First call
		self.FetchPage(1,unique=True)

	def AddCallback(self,callback):
		self.deferred.addCallback(callback)

	def FetchPage(self,page,unique = False):
		"""Issue fetching a single page"""
		deferred = self.api.APIRequest(self.cmd,page=page,unique=unique,**self.params).addErrback(self.DErrPage,page=page)
		deferred.addCallback(self.DGetPage,page=page)
		self.pages_pending[page] = deferred

	def Errback(self,result):
		print "Errback",result,page
		return

	def DErrPage(self,page):
		self.revert = True

	def DGetPage(self,result,page=0):
		code = result.pop("code",None)
		if code == None:
			print "DGetPage has no code",result.keys()
		phrase = result.pop("phrase","")
		errors = result.pop("errors",None)
		pages = result.pop("page",None)

	# Handle page
		try:
			if pages == None:
				self.pages_pending = {}
				self.burst_pages = {}
				self.revert = True
			else:
				self.pages_pending.pop(page,None)
				fpage = self.pages_fetched.get(page,None)
				if fpage == None:
					self.pages_fetched[page] = 0
				self.pages_fetched[page] += 1

				print "\nSuccess",code,phrase,page,pages,result.get("call")
	#		print result.keys()
	#		print self.pages_fetched,self.pages_pending

			# Process Results (Check if callback want's more data, register items, count unknowns)
				p = {"fetched":self.pages_fetched,"pages":pages,"items":len(self.items)} # 'Progress'
				pitems = self.ProcessPageResults(result)	# Returns a dict of hash --> item for the current page
				pfinished = self.page_callback(result,p)	# Feed the dictified result to the callback, which decides if it want's more pages
				self.burst_pages[page] = pfinished		# Register finishing request for this burst
				unknowns = self.RegisterPageResults(pitems)	# Store results, get number of previously unknown items

				if pfinished == False:		# If the callback want's more data, check if page 1 indicates changed data
					if page == 1:
						if unknowns > 0:
							self.revert = True	

		# Issue new Fetches
			if len(self.pages_pending) == 0 :
				fpages = []
				# If revert = True, refetch last burst
				if self.revert == True and self.complete == True: 
					fpages = self.burst_pages.keys() # Redo last fetch
					if 1 not in fpages:				 # Ensure page 1 is present
						fpages.append(1)
				else:
					maxfetched,lastpage = max(self.pages_fetched.keys()),pages["last"]	# sets Range to be fetched
					self.burst_pages.pop(1,None)	# Don't care if page 1 returned "finished" from callback
					finished = False
					for pf in self.burst_pages.values():
						if pf == True:
							finished = True
					if finished != True and maxfetched < lastpage:
						fpages = range(maxfetched+1,min(maxfetched+1+self.bsize,lastpage+1))
						if self.complete == True and 1 not in fpages:
							fpages.append(1)
					else:
						self.deferred.callback(self.items)
						print "Finished",self.pages_fetched

				# Reset for new Burst
				self.burst_pages = {}	# Reset Burst
				self.revert = False
				# Issue new Burst
				for p in fpages:
					if p == 1:
						self.FetchPage(p,unique=True)
					else:
						self.FetchPage(p)

		except Exception as e:
			import sys, os, traceback
			print "DGetPage had an error"
			exc_type, exc_obj, exc_tb = sys.exc_info()
	#			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			print exc_type, exc_tb.tb_lineno, exc_tb
			traceback.print_tb(exc_tb)
			self.deferred.errback(e)

	def ProcessPageResults(self,result):
		print "Implement ProcessPageResults"

	def RegisterPageResults(self,items):
		unknowns = 0
		for h,item in items.items():
			oitem = self.items.get(h,None)
			if oitem == None:
				unknowns += 1
			self.items[h] = item
		return unknowns

class FetchLedger(MultipageFetchSession):
	def __init__(self,api,complete=False,**kwargs):
		super(FetchLedger,self).__init__(api,"showAccountLedger",complete=complete,**kwargs)

	def ProcessPageResults(self,result):
		ledger = result.get(u'account_ledger',None)
		items = {}
		if ledger != None:
			for item in ledger:
				h = hash(item["date"])+hash(item["cashflow"])+hash(item["type"])
				if h in items.keys():
					print "Double",item
				items[h] = item
		return items

class FetchMyTrades(MultipageFetchSession):
	def __init__(self,api,complete=False,**kwargs):
		super(FetchMyTrades,self).__init__(api,"showMyTrades",complete=complete,**kwargs)

	def ProcessPageResults(self,result):
		trades = result.get(u'trades',None)
		items = {}
		if trades != None:
			for trade in trades:
				tid = trade.get(u'trade_id')
				items[tid] = trade
		return items

class FetchMyOrders(MultipageFetchSession):
	def __init__(self,api,complete=False,**kwargs):
		super(FetchMyOrders,self).__init__(api,"showMyOrders",complete=complete,**kwargs)

	def ProcessPageResults(self,result):
		orders = result.get(u'orders',None)
		items = {}
		if orders != None:
			for order in orders:
				oid = order.get(u'order_id')
				items[oid] = order
		return items