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
from twisted.web.iweb import IBodyProducer
from twisted.internet.defer import Deferred
from twisted.web.client import Agent,readBody,WebClientContextFactory,HTTPConnectionPool
from twisted.web.http_headers import Headers
from twisted.internet import reactor
from twisted.internet.protocol import Protocol
	

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
		self.calls['showMyOrders'] = ['GET',orderuri+'/my_own',{'type':['sell','buy']},2]
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
			for key, value in sorted(params.iteritems()):
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
			bodyProducer = StringProducer(u''+encoded_string)
			
		d = self.agent.request(method,url,headers=h,bodyProducer=bodyProducer)
		d.addCallback(self.APIResponse,eid=eid)
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
	
	def CalcHash(self,method,uri,params):
		h = 0
		for p in params.values():
			h += hash(p)
		return hash(method)+hash(uri)+h
		
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
		dt = 0.2	# Explicit pause inbetween two back to back request to avoid bad nonces
		if self.CreditsAvailable(3):
			for k,req in self.Queue():
				if k not in self.pending.keys():
					if req.attempts < 10:
						self.APIConnect(req.method,req.params,req.uri,k) # Chaining of APIResponse is done in this function, so returned deferred is not used.
						self.pending[k] = req.credits
						req.Send()
					else:
						req.DeliverResult({"error":"too many unsuccesful attempts","attempts":req.attempts})
						self.DeleteRequest(k)
					break
		else:
			dt = 2
		if len(self.queue) > len(self.pending):
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
	
	def CreditsAvailable(self,credits):
		ct = time.time()
		dt = ct-self.lasttime
		available = min(20,self.lastcredits+dt)-sum(self.pending.values())
	#	print "inflight:",sum(self.pending.values()),"credits",available
		if available > 2+credits:
			return True
		else:
			return False
	
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
		response.deliverBody(BtcdeAPIProtocol(finished))
		req = self.queue[eid]
		header = {"code":response.code,"phrase":response.phrase,"call":req.method+":"+req.uri,"reqID":eid}
		if response.code == 200 or response.code == 201:
			finished.addCallback(self.DequeueAPIRequest,eid=eid,header=header)
		else:
			self.lastcredits -= self.pending[eid]
			if response.code == 429 or response.code == 403:	# 403 might need some extra handling
				retry = int(response.headers.getRawHeaders("Retry-After")[0])
				header["retry"] = retry
				self.Reenqueue(eid)
				self.ScheduleNextIssue(retry+3)
				finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
				
			else:
				d = finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
				if response.code == 400:
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
		
class PriorityBitcoinDeAPI(QueuedBitcoinDeAPI):
	def Queue(self):
		q = sorted(self.queue.items(),key=lambda x : (-x[1].priority,x[1].eid))
		return q
	
class StringProducer(IBodyProducer):
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
			data = loads(self.partial)	#json.loads
		except:
			print "JSON error",self.partial
			self.deferred.errback(["JSON data couldn't be loaded properly",self.partial[-20:]])
		else:
			self.deferred.callback(data)
