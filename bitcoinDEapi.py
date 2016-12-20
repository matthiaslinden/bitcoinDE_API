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

import json
import time
import hashlib
import hmac

# Building upon twisted 
from twisted.web.iweb import IBodyProducer
from twisted.internet.defer import Deferred
from twisted.web.client import Agent,readBody,WebClientContextFactory
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
#		pool = HTTPConnectionPool(reactor)		# Actually reusing the connection leads to correct credits
#		pool.maxPersistentPerHost = 2
		self.contextFactory = WebClientContextFactory()
		self.agent = Agent(self.reactor, self.contextFactory)#,pool=pool)
		
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
			return self.EnqueAPIRequest(method,kwargs,uri,credits)
		else:
			# Unknown request
			d = Deferred()
			d.errback(["Unknown request"])
			return d
	
	def EnqueAPIRequest(self,method,params,uri,credits):
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
			md5_encoded_query_string = hashlib.md5(encoded_string).hexdigest()
		else:
			md5_encoded_query_string = hashlib.md5('').hexdigest()
		
		hmac_data = method + '#' + url + '#' + self.api_key + '#' + str(self.nonce) + '#' + md5_encoded_query_string
		hmac_signed = hmac.new(self.api_secret,digestmod=hashlib.sha256, msg=hmac_data).hexdigest()
			
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
			
		d = self.agent.request("GET",url,headers=h,bodyProducer=bodyProducer)
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
			finished.addCallback(self.DequeueAPIErrors,eid=eid,header=header)
		return finished
		
	def DequeueAPIRequest(self,response,eid,header):
		"""Append header fields (code,phrase) to the actual data"""
		r = response
		r.update(header)
		return r
		
	def DequeueAPIErrors(self,response,eid,header):
		"""Pick error info (message,code) from Response.body and return it along with the header (code,phrase,[retry])"""
		try:
			errors = response.get("errors",[{}])[0]
		except:
			header["message"] = "Unknown error"
			return header
		else:
			header["errmessage"] = errors.get("message","")
			header["errcode"] = errors.get("code",-1)
			return header
	

class QueuedAPIRequest(object):
	def __init__(self,eid,rhash,method,uri,params,credits,data,deferred):
		self.eid = eid
		self.rhash = rhash
		self.method=method
		self.uri=uri
		self.params=params
		self.credits = credits
		self.deferreds = [deferred]
		
	def AddDeferred(self,deferred):
		self.deferreds.append(deferred)
		
	def DeliverResult(self,result):
		for d in self.deferreds:
			d.callback(result)
	
	def __del__(self):
		for d in self.deferreds:
			d.errback("no result during lifetime")
		
	
class QueuedBitcoinDeAPI(BitcoinDeAPI):
	"""Implements a Queue that holds requests and manages credits"""
	def __init__(self,reactor,api_key,api_secret):
		super(QueuedBitcoinDeAPI,self).__init__(reactor,api_key,api_secret)
		
		self.requestID = 0
		self.queue = {}
		self.pending = {}
	
	def CalcHash(self,method,uri,params):
		h = 0
		for p in params.values():
			h += hash(p)
		return hash(method)+hash(uri)+h
		
	def SameHashInQueue(self,h):
		for k,req in self.queue.items():
			if req.rhash == h:
				return k,req
		return 0,None
		
	def StartRequests(self):
		for k,req in self.queue.items():
			if k not in self.pending.keys():
				if self.CreditsAvailable(req.credits):
					self.APIConnect(req.method,req.params,req.uri,k)#.chainDeferred(req.deferred)
					self.pending[k] = req.credits
	
	def CreditsAvailable(self,credits):
		print "inflight:",sum(self.pending.values())
		return True
	
	def EnqueAPIRequest(self,method,params,uri,credits,data):
		finished = Deferred()
		
		h = self.CalcHash(method,uri,params)
		
		samereqID,samereq = self.SameHashInQueue(h)	# Return same request if already enqueued or pending
		if samereqID == 0:	# unique request
			eid = self.requestID
			self.requestID += 1
			request = QueuedAPIRequest(eid,h,method,uri,params,credits,data,finished)
			self.queue[eid] = request
		else:
			samereq.AddDeferred(finished)	# deferred to same request's chain
		
		self.StartRequests()
		return finished
	
	def APIResponse(self,response,eid):
		"""Handle the returned HTTP-Header"""
		finished = Deferred()
		if response.code == 200:	# Request OK, read body
			response.deliverBody(BtcdeAPIProtocol(finished))
			finished.addCallback(self.DequeueAPIRequest,eid=eid)
		elif response.code == 201:	# Request successfull
			finished.callback([response.code])
		elif response.code == 400:	# Bad Request
			finished.errback([response.code,response.phrase])
		elif response.code == 429:	# Too many requests, no credits available
			pass
		elif response.code == 403:	# Forbidden, temporarily banned
			pass
		elif response.code == 404:	# Entity not found
			pass
		elif response.code == 422:	# Not processed successfully
			pass
		else:
			print "unhandled Response",response.code,response.phrase
			finished.errback(["Unhandled response code",response.code,response.phrase])
		return finished
	
	def DequeueAPIRequest(self,response,eid):
		"""Handle credits, errors, pages after Protocol has received all data"""
	#	finished = Deferred()
	#	print eid,response["credits"],response["errors"]#,response["page"]
#		print self.pending[eid]
	#	del response["credits"]
	#	del response["errors"]
	#	if len(response.keys()) == 1:
	#		finished.callback(response[response.keys()[0]])
	#	else:
	#		finished.callback(response)
		self.queue	[eid].DeliverResult(response)
	#	for d in self.requests[eid].deferreds:
	#		d.callback(response)
	#	return finished
	
	def APIRequestPages(self,call,pages,**kwargs):
		"""Request a certain number of pages
			- As Requests might take some time due to limited credits, pages are requested in blocks 
		"""
		pass
	

class StringProducer(IBodyProducer):
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
			self.deferred.callback(json.loads(self.partial))
		except e:
			print "JSON error"
			self.deferred.errback(["JSON data couldn't be loaded properly",self.partial[-20:]])
	
