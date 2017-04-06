#!/usr/bin/env python2.7
#coding:utf-8

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

from time import time
#import sys
from hashlib import sha1
from json import loads

from os import urandom
from base64 import b64encode	# Websocket Key handling
from struct import unpack	# Websocket Length handling

from twisted.python import log

from twisted.internet import endpoints,reactor	# unfortunately reactor is neede in ClientIo0916Protocol
from twisted.internet.ssl import optionsForClientTLS
from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.protocol import Protocol, Factory
from twisted.application.internet import ClientService
from twisted.protocols import basic

class ClientIo0916Protocol(basic.LineReceiver):
	
	_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"	# Handshake key signing
	
	def connectionMade(self):
		
		self.state = 0
		self.http_pos = ""
		self.http_length = 0
		
		self.pongcount = 0
		self.pingcount = 0
		self.lastpingat = 0
		self.pinginterval = 0
		
		self.setLineMode()
		print "connectionMade"
		data = "GET /socket.io/1/?t=%d HTTP/1.1\n"%(time()*1000)
		self.sendLine(data)
		
	def Heartbeat(self):
		self.pongcount += 1
		pong = bytearray([129,3])+bytes("2::")
	#	pong = bytearray([1,3])+bytes("2::") # Produces more reconnects 
		self.transport.write(bytes(pong))
	
	
	def ParseHTTP(self,line):
		nonce,t1,t2,options = line.split(":")
		if "websocket" in options:
			if len(nonce) == 20:
				self.websocket_key = b64encode(urandom(16))
				data = "GET /socket.io/1/websocket/%s HTTP/1.1\r\n"%nonce
			#	data += "Accept-Encoding: gzip, deflate, sdch"
				data += "Connection: Upgrade\r\nUpgrade: websocket\r\n"
				data += "Sec-WebSocket-Key: %s\r\n"%self.websocket_key
				data += "Sec-WebSocket-Version: 13\r\n"
				data += "Sec-WebSocket-Extensions: \r\n"
			#	data += "Sec-WebSocket-Extensions: permessage-deflate\r\n";
				data += "Pragma: no-cache\r\nCache-Control: no-cache\r\n"
				self.sendLine(data)
				self.state = 1
		
	def eatUpHTTP(self,line):
		if self.http_pos == "head":
			if line == "":
				self.http_pos = "length"
		elif self.http_pos == "length":
			self.http_length = int(line)
			self.http_pos = "content"
		elif self.http_pos == "content":
			if line != "":
				self.ParseHTTP(line)
				self.http_length -= len(line)
				if self.http_length <= 0:
					self.http_pos = "tail"
			else:
				self.http_pos = "tail"
		elif self.http_pos == "tail":
			if line == "":
				self.http_pos = ""
	
	def rawDataReceived(self,data):
	#	print bin(ord(data[0])),(ord(data[0])&0b1110)/2
		if self.state == 2:
			self.state = 3
		elif self.state == 3:
			# Calculate the length
			l,b = ord(data[1])&(0b1111111),2	# Handle the length field
			if l == 126:
				b = 4
				l = unpack('!H',data[2:4])[0]	# struct.unpack
			elif l == 127:
				b = 10
				l = unpack('!Q',data[2:10])[0]
		# Different Opcodes
			if data[b] == "1":
				print data
			elif data[b] == "2":
				self.pingcount += 1
				self.ProcessPing(data[b:])
					
			elif data[b] == "5":
				self.onPacketReceived(data[b:],l-b)
			else:
				print "unknown opcode",data
			reactor.callLater(25,self.Heartbeat)
		else:
			print "Unknwon state",self.state
	
	def ProcessPing(self,data):
		since = 0
		now = time()
		if self.lastpingat != 0:
			since = now-self.lastpingat
			self.pinginterval = since
		self.lastpingat = now
#		print "---> ping",self.pingcount,"%0.2f"%since,"\t",data
	
	def lineReceived(self,line):
		if "HTTP/1.1" in line:
			lc = line.split(" ")
			http,code,phrase = lc[0],int(lc[1])," ".join(lc[2:])
			if code == 200:
				self.http_pos = "head"
			elif code == 101:
				self.state = 1
			else:
				self.http_pos = ""
				self.Terminate([code,phrase])
		elif self.state == 0:
			self.eatUpHTTP(line)
		elif self.state == 1:
			if "Sec-WebSocket-Accept:" in line:
				key_got = line.split(" ")[1]
				mysha1 = sha1()
				mysha1.update(self.websocket_key + self._MAGIC)
				key_accept = b64encode(mysha1.digest())
				if key_got == key_accept:
					self.state = 2
				else:
					self.Terminate(["key missmatch",key_got,key_accept])
			elif "Upgrade:" in line:
				if line.split(" ")[1] != "websocket":
					self.Terminate(["Upgrade to websocket failed",line])
		elif self.state == 2:
			if line == "":		
				self.setRawMode()	# Wait for the packet to end
			else:
				print "Should never happen",len(line),line
		else:
			print "unexpected:",line
	# 
	def Terminate(self,reason):
	 	print "Terminate",reason
		
	def onPacketReceived(self,data,length):
		print "Packet",length,data
	
	def connectionLost(self,reason):
		print "WSconnectionLost",reason

		
class WSjsonBitcoinDEProtocol(ClientIo0916Protocol):
	def onPacketReceived(self,data,length):
		i = 1
		while data[i] == ":":
			i+=1
		jdata = loads(data[i:])	# json.loads
		otype,args = jdata["name"],jdata["args"][0]
		self.onEvent(otype,args)
		
	def onEvent(self,name,args):
		print "Implement onEvent!"
		
	def Terminate(self,reason):
	 	print "WSjson Terminate",reason
    
	def connectionLost(self,reason):
		print "WSjson connectionLost",reason
	
class BitcoinDEProtocol(WSjsonBitcoinDEProtocol):
	def __init__(self):
		self._iadd = ['uid','seat_of_bank_of_creator','id','type','payment_option','is_trade_by_fidor_reservation_allowed','bic_full','order_id','price','amount','min_amount','is_shorting_allowed','order_type','is_kyc_full','is_shorting','is_trade_by_sepa_allowed','fidor_account','min_trust_level','amount','only_kyc_full']
		self._irem = ['trade_user_id', 'order_id', 'reason', 'type', 'id', 'order_type','amount','price']
		
	def onEvent(self,name,args):
		if name == "add_order":
			D = {}
			for a in self._iadd:
				D[a] = args.get(a,"")
			self.factory.addOrder(D)
		elif name == "remove_order":
			D = {}
			for a in self._irem:
				D[a] = args.get(a,"")
			self.factory.removeOrder(D)
		elif name == "refresh_express_option":
			# refresh_express_option {u'4120760': {u'is_trade_by_sepa_allowed': u'0', u'is_trade_by_fidor_reservation_allowed': u'1'}}
			D = {"name":name,"args":args}
			self.factory.updateOrder(D)
			
		elif name == "skn":
			# unknown Event skn {u'uid': u'0yybQJoPJQkkfFW7rsg.'}	16:24
			# unknown Event skn {u'uid': u'0yybQJoIpggjfFWurrA.'}
			self.factory.skn(args.get("uid",""))
		else:
				
			print "unknown Event",name,args
			
	

class BitcoinDEMarket(Factory):
	"""Simple factory that holds an orderbook"""
	protocol = BitcoinDEProtocol
	
	def startFactory(self):
		self.Orderbook = {}
	
	def startedConnecting(self,connector):
		print "\tServerWS\tConnected",connector
	
	def addOrder(self,args):
		print "addOrder",args
		self.Orderbook[args["id"]] = [args["uid"],args["amount"],args["min_amount"],args["price"]]
		
	def removeOrder(self,args):
		print "removeOreder",args
		if args["id"] in self.Orderbook.keys():
			del self.Orderbook[args["id"]]
			print len(self.Orderbook)
		else:
			print "Order",args["id"],"unknown",args
			
	def updateOrder(self,args):
		pass
		
	def skn(self,uid):
		pass
	
class BitcoinDESubscribeFactory(Factory):
	"""Factory that enables subscription services for marketchanges. Registers callbacks to add/rm/management."""
	protocol = BitcoinDEProtocol
	def __init__(self):
		self.addfunc = []	# List of registered callbacks
		self.rmfunc = []
		self.mngmtfunc = []
		self.updatefunc = []
		
		self.counter = {"add":0,"remove":0,"taken":0,"update":0,"skn":0}
	
	def startedConnecting(self,connector):
		print "\tServerWS\tConnected",connector
			
	def Lost(self):
		print "\tServerWS client called lost"
	
	def connectionLost(self,connector,reason):
		print "\tServerWS connectionList",connector,reason
	
	def addOrder(self,args):
		args["update"] = "add"
		for func in self.addfunc:
			func(args)
		self.counter["add"] += 1
			
	def updateOrder(self,args):
		args["update"] = "update"
		for func in self.updatefunc:
			func(args)
		self.counter["update"] += 1
			
	def removeOrder(self,args):
	# 0yzjOGFy3vQgfFm0re8. is the special 'reason'-id for removed trades
		
		reason = args.get("reason",None)
		if reason == "0yzjOGFy3vQgfFm0re8.":
			args["reason"] = "remove"
			self.counter["remove"] += 1
		else:
			self.counter["taken"] += 1
			
		args["update"] = "remove"
		for func in self.rmfunc:
			func(args)
			
	def skn(self,uid):
		for func in self.mngmtfunc:
			func({"action":"skn","uid":uid})
		self.counter["skn"] += 1
			
	def SubscribeAdd(self,func):
		self.addfunc.append(func)
		
	def SubscribeRemove(self,func):
		self.rmfunc.append(func)
		
	def SubscribeManagement(self,func):
		self.mngmtfunc.append(func)
		
	def SubscribeUpdate(self,func):
		self.updatefunc.append(func)
	
class BitcoinDESubscribe(object):
	"""Offers subscription services to the bitcoin.de websocket-API.
Register Callback functions in case a market event occures.
Opens a https connection.
Mostly used as a proxy to the underlying subscription-aware factory."""
	def __init__(self,reactor):
		self.reactor = reactor
		
		print "BitcoinDESubscribeFactory - constructor"
		
		tlsctx = optionsForClientTLS(u'ws.bitcoin.de',None)
		self.endpoint = endpoints.SSL4ClientEndpoint(self.reactor, 'ws.bitcoin.de', 443,tlsctx)
		self.factory = BitcoinDESubscribeFactory()
		
		self.connService = ClientService(self.endpoint,self.factory)
		self.connService.startService()
	
# Proxies
	def SubscribeAdd(self,func):
		return self.factory.SubscribeAdd(func)
		
	def SubscribeRemove(self,func):
		return self.factory.SubscribeRemove(func)
	
	def SubscribeManagement(self,func):
		return self.factory.SubscribeManagement(func)
	
	def SubscribeUpdate(self,func):
		return self.factory.SubscribeUpdate(func)

def main():
	from twisted.internet import reactor,ssl
	# Simple 'TrackMarket' example displaying all events processed
	tlsctx = optionsForClientTLS(u'ws.bitcoin.de',None)
	endpoint = endpoints.SSL4ClientEndpoint(reactor, 'ws.bitcoin.de', 443,tlsctx)
	factory = BitcoinDEMarket()
	
	connService = ClientService(endpoint,factory)
	connService.startService()
	
	reactor.run()
	
if __name__ == '__main__':
	main()
	

