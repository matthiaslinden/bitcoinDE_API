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


class bitcoinDeAPIpendingTransaction(object):
	def __init__(self):
		pass
	
class bitcoinDeAPIpending(object):
	def __init__(self):
		# Handle enqueued request
		self.requestID = 0
		self.pending = {}
	
	def EnqueueTransaction(self,*args):
		self.requestID += 1
		rID = self.requestID
		
		return rID
	
	def DequeueTransaction(self,rID):
		pass
	

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
		self.contextFactory = WebClientContextFactory()
		self.agent = Agent(self.reactor, self.contextFactory)
		
		self.api_key = api_key
		self.api_secret = api_secret
		
		self.calls = {}
		# Method,uri,required params with allowed values,credits,field to return (after credits/pages are stripped)
		# Orders
		self.calls['showOrderbook'] = ['GET',orderuri,{'type':['sell','buy']},2,'']
		self.calls['showOrderbookCompact'] = ['GET',orderuri+'/compact',{},3,'']
		self.calls['createOrder'] = ['POST',orderuri,{'type':['sell','buy'],'max_amount':[],'price':[]},1,'']
		self.calls['deleteOrder'] = ['DELETE',orderuri,{'order_id':[]},2,'']
		self.calls['showMyOrders'] = ['GET',orderuri+'/my_own',{'type':['sell','buy']},2,'']
		self.calls['showMyOrderDetails'] = ['GET',orderuri,{'order_id':[]},2,'']
		# Trades
		self.calls['executeTrade'] = ['POST',tradeuri,{'order_id':[],'amount':[]},1,'']
		self.calls['showMyTradeDetails'] = ['GET',tradeuri,{'trade_id':[]},3,'']
		self.calls['showMyTrades'] = ['GET',tradeuri,{},3,'']
		self.calls['showPublicTradeHistory'] = ['GET',tradeuri+'/history',{'since_tid':[]},3,'']
		# Account
		self.calls['showAccountInfo'] = ['GET',accounturi,{},2,'data']
		self.calls['showAccountLedger'] = ['GET',accounturi+'/ledger',{},3,'trades']
		# Other
		self.calls['showRates'] = ['GET',apihost+'/'+apiversion+'/rates',{},3,'']
		
	
	def APIRequest(self,call,**kwargs):
		"""Compiles the Request"""
		if call in self.calls.keys():
			method,uri,required,credits,data = self.calls[call]
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
			return self.EnqueAPIRequest(method,kwargs,uri,credits,data)
		else:
			# Unknown request
			d = Deferred()
			d.errback(["Unknown request"])
			return d
	
	def EnqueAPIRequest(self,method,params,uri,credits,data):
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
		if response.code == 200:
			finished = Deferred()
			response.deliverBody(BtcdeAPIProtocol(finished))
			finished.addCallback(self.DequeueAPIRequest,eid=eid)
			return finished
		else:
			return {"code":response.code,"phrase":response.phrase}
		
	def DequeueAPIRequest(self,response,eid):
		return response

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
	
	
