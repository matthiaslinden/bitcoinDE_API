#!/usr/bin/env python2.7
#coding:utf-8

import json
import time
import hashlib
import hmac

from twisted.web.iweb import IBodyProducer
from twisted.internet.defer import Deferred
from twisted.web.client import Agent,readBody,WebClientContextFactory
from twisted.web.http_headers import Headers
from twisted.internet import reactor
from twisted.internet.protocol import Protocol


class bitcoinDeAPI(object):
	
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
		# Method,uri,required params with allowed values,field to return (after credits/pages are stripped)
		# Orders
		self.calls['showOrderbook'] = ['GET',orderuri,{'type':['sell','buy']},2,'']
		self.calls['showOrderbookCompact'] = ['GET',orderuri+'/compact',{},3,'']
		self.calls['createOrder'] = ['POST',orderuri,{'type':['sell','buy'],'max_amount':[],'price':[]},1,'']
		self.calls['deleteOrder'] = ['DELETE',orderuri,{'order_id':[]},2,'']
		self.calls['showMyOrders'] = ['GET',orderuri+'/my_own',{'type':['sell','buy']},2,'']
		self.calls['showMyOrderDetails'] = ['GET',orderuri,{'order_id':[]},2,'']
		# Trades
		self.calls['executeTrade'] = ['POST',tradeuri,{'order_id':[],'amount':[]},1,'']
		self.calls['showMyTradeDetails'] = ['POST',tradeuri,{'order_id':[]},3,'']
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
				else:
					pass	# executeTrade,showMyTradeDetails requires order_id + params 
			return self.EnqueRequest(method,kwargs,uri,data)
		else:
			d = Deferred()
			d.callback(None)
			return d

	def EnqueRequest(self,method,params,uri,data):
		# TODO: remove data from APIconnect Chain and into queue
		return self.APIConnect(method,params,uri)

	def APIConnect(self,method,params,uri):
		"""Encapsulates all the API encoding"""
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
			bodyProducer = StringProducer(encoded_string)
		return self.agent.request("GET",url,headers=h,bodyProducer=bodyProducer).addCallback(self.APIResponse)
	
	def APIResponse(self,response):
		finished = Deferred()
		print response.code,response.phrase
		if response.code == 200:
			response.deliverBody(BtcdeProtocol(finished))
			finished.addCallback(self.DequeueRequest)
		elif response.code == 201:
			print "Request successfull"
			finished.callback([response.code])
		return finished
		
	def DequeueRequest(self,response):
		finished = Deferred()
		print response["credits"],response["errors"],response["page"]
		del response["credits"]
		del response["errors"]
		if len(response.keys()) == 1:
			finished.callback(response[response.keys()[0]])
		else:
			finished.callback(response)
		return finished

class BtcdeProtocol(Protocol):
	def __init__(self,deferred):
		self.deferred = deferred
		self.partial = ""
		print "BTCdeProtocol"
	
	def dataReceived(self,data):
		self.partial += data
		
	def connectionLost(self,reason):
	#	print "ConnectionLost",reason
		self.deferred.callback(json.loads(self.partial))
	
