#!/usr/bin/env python3.7
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
from hashlib import sha1
from json import loads

from os import urandom
from base64 import b64encode	# Websocket Key handling
from struct import unpack	# Websocket Length handling

from twisted.python import log

from twisted.internet import endpoints,reactor,task			# unfortunately reactor is neede in ClientIo0916Protocol
from twisted.internet.ssl import optionsForClientTLS
from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.protocol import Protocol, Factory
from twisted.application.internet import ClientService
from twisted.protocols import basic


class ClientIo0916Protocol(basic.LineReceiver):
	"""Implements a receiver able to interact with the websocket part of a JS clientIO server.
Requests options from the clientIO server and if websocket is avaiable, upgrades the connection 'talk' websocket.
After actin as a basic.LineReceiver to process the http GET,UPGRADE part (lineReceived), switch to RAW mode (rawDataReceived)."""
	_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"	# Handshake key signing
	
	def connectionMade(self):
		""" Called after the factory that was passed this protocol established the connection. """
		self.state = 0		# pseudo state-machine to keep track which phase http-upgrade-websocket the connection is in
		self.http_pos = ""
		self.http_length = 0
		
		self.pongcount = 0
		self.pingcount = 0
		self.lastpingat = 0
		self.pinginterval = 0
		
		self.setLineMode()	# for the http part, process the packet line-wise
		data = "GET /socket.io/1/?t=%d HTTP/1.1\n"%(time()*1000)
		self.sendLine(data.encode('utf8'))	# first GET request
		
	def Heartbeat(self):
		self.pongcount += 1
		pong = bytearray([129,3])+b"2::"
	#	pong = bytearray([1,3])+bytes("2::") # Produces more reconnects 
		self.transport.write(bytes(pong))
	
	
	def ParseHTTP(self,line):
		"""Processes the response to the GET Request and create the UPGRADE Request"""
		nonce,t1,t2,options = line.split(":")
		if "websocket" in options:
			if len(nonce) == 20:
				key = b64encode(urandom(16))
				self.websocket_key = key.decode('utf8')
				data = "GET /socket.io/1/websocket/%s HTTP/1.1\r\n"%nonce
			#	data += "Accept-Encoding: gzip, deflate, sdch"
				data += "Connection: Upgrade\r\nUpgrade: websocket\r\n"
				data += "Sec-WebSocket-Key: %s\r\n"%(self.websocket_key)
				data += "Sec-WebSocket-Version: 13\r\n"
				data += "Sec-WebSocket-Extensions: \r\n"
			#	data += "Sec-WebSocket-Extensions: permessage-deflate\r\n";
				data += "Pragma: no-cache\r\nCache-Control: no-cache\r\n"
#				self.transport.write(data.encode('utf8'))
				self.sendLine(data.encode('utf8'))
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
		t = time()
		if type(data) == str:
			data = bytearray(data)
		if self.state == 2:
			self.state = 3
		elif self.state == 3:
			# Calculate the length
			l,b = data[1]&(0b1111111),2	# Handle the length field
	
			if l == 126:
				b = 4
				l = unpack('!H',data[2:4])[0]	# struct.unpack
			elif l == 127:
				b = 10
				l = unpack('!Q',data[2:10])[0]
		# Different Opcodes
		
			if data[b] == 47:
				print(data)
			elif data[b] == 48:
				self.pingcount += 1
				self.ProcessPing(data[b:])
				
			elif data[b] == 53:
				self.onPacketReceived(data[b:].decode("utf8"),l-b,t)
			else:
				print("unknown opcode",data)
			reactor.callLater(25,self.Heartbeat)
		else:
			print("Unknwon state",self.state)
	
	def ProcessPing(self,data):
		since = 0
		now = time()
		if self.lastpingat != 0:
			since = now-self.lastpingat
			self.pinginterval = since
		self.lastpingat = now
#		print "---> ping",self.pingcount,"%0.2f"%since,"\t",data
	
	def lineReceived(self,line):
		"""Parse the http Packet (linewise) and switch states accordingly"""
		line = line.decode('utf8')
		if "HTTP/1.1" in line:	# First line in response
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
				mysha1.update(self.websocket_key.encode('utf8') + self._MAGIC)
				key_accept = b64encode(mysha1.digest()).decode('utf8')
				if key_got == key_accept:
					print("WS 0.9 connection accepted")
					self.state = 2
				else:
					self.Terminate(["key missmatch",key_got,key_accept])
			elif "Upgrade:" in line:
				if line.split(" ")[1] != "websocket":
					self.Terminate(["Upgrade to websocket failed",line])
		elif self.state == 2:
			if line == "":		# Wait for the packet to end
				self.setRawMode()	
			else:
				print("Should never happen",len(line),line)
		else:
			print("unexpected:",line)
	# 
	def Terminate(self,reason):
	 	print("Terminate",reason)
		
	def onPacketReceived(self,data,length,t):
		""" Dummy, implement Your own websocket-packet-processing"""
		print("Packet",length,data)
	
	def connectionLost(self,reason):
		print("WSconnectionLost",reason)

		
class WSjsonBitcoinDEProtocol(ClientIo0916Protocol):
	""" Processes the Content of the Websocket packet treating it as JSON and pass the dict to an onEvent-function mimmicing the original js behaviour"""
	def onPacketReceived(self,data,length,t):
		i = 1
		while data[i] == ":":
			i+=1
		jdata = loads(data[i:])	# json.loads
		otype,args = jdata["name"],jdata["args"][0]
		self.factory.onEvent(otype,args,t)
		
	def Terminate(self,reason):
	 	print("WSjson Terminate",reason)

	def connectionLost(self,reason):
		print("WSjson connectionLost",reason)

# * * * * * * * * * * * socket.io > 2.0 Implementation * * * * * * * * * * * #

class ClientIo2011Protocol(basic.LineReceiver):
	_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"	# Handshake key signing
	
	def connectionMade(self):
		self.nonce = time()*1000
		self.header = [{"lines":[]}]
		self.eio = 3
		
		self.pingInterval = 20
		self.pingcount = 0
		self.nextlen = 0
		
		self.setLineMode()
		self.sendInit()
		
	def sendInit(self):
		cookie = self.header[-1].get("cookie","")
		data = ""
		if len(cookie) > 0:
			cookie = "&io="+cookie
		data = "GET /socket.io/1/?EIO=%d%s&t=%d-0&transport=polling HTTP/1.1\r\n"%(self.eio,cookie,self.nonce)
		self.sendLine(data.encode('utf8'))	# first GET request
		

	def lineReceived(self,line):
		if len(line) > 3:
			self.header[-1]["lines"].append(line)
		else:
			self.onHTTPHeader()
		
	def onHTTPHeader(self):
		upgrade = False
		header = self.header[-1]
		code = ""
		for line in header["lines"]:
			line = line.decode('utf8')
			if "HTTP/1.1" in line:
				words = line.split("HTTP/1.1 ")[1].split(" ")
				code,word = words[0]," ".join(words[1:])
				header["code"] = code
			if "Set-Cookie:" in line:
				cookie = line.split("=")[1].split(";")[0]
				header["cookie"] = cookie
			elif '"upgrades"' in line:
				upgrade = True
			elif "Upgrade:" in line:
				header["Upgrade"] = line.split(": ")[-1]
			elif "Sec-WebSocket-Accept" in line:
				header["Key"] = line.split(": ")[-1]
			
			if "pingInterval" in line:	# Retrieve pingInterval from Response
				inter = line.split("pingInterval")
				if len(inter) > 1:
					self.pingInterval = int(inter[1].split(":")[1].split(",")[0])/1100
					
		if code == "200":
			if upgrade == False:
				self.sendInit()
			else:
				self.sendUpgrade()
		elif code == "101":
			self.checkWebsocket()
		else:
			print(code,header)
		
		self.header.append({"lines":[]})
		
	def sendUpgrade(self):
		key = b64encode(urandom(16))
		self.websocket_key = key
		data = "GET /socket.io/1/?EIO=%d&transport=websocket&t=%d-2%s HTTP/1.1\r\n"%(self.eio,time()*1000,self.header[-1].get("cookie",""))
		data += "Connection: Upgrade\r\nUpgrade: Websocket\r\n"
		data += "Sec-WebSocket-Key: %s\r\n"%(self.websocket_key.decode('utf8'))
		data += "Sec-WebSocket-Version: 13\r\n"
		data += "Pragma: no-cache\r\nCache-Control: no-cache\r\n"		
		self.sendLine(data.encode('utf8'))
		
	def checkWebsocket(self):
		key_got = self.header[-1].get("Key","")
		mysha1 = sha1()
		mysha1.update(self.websocket_key + self._MAGIC)
		key_accept = b64encode(mysha1.digest()).decode('utf8')
		if key_got == key_accept:
			self.setRawMode()
			reactor.callLater(3,self.sendPing)
			reactor.callLater(2,self.requestMarket)
			print("WS 2.0 connection accepted")
		
	def rawDataReceived(self,data):
		if type(data) == str:
			data = bytearray(data)
		
		t = time()
		if len(data) > 4:
#			print self.nextlen,map(ord,data[:8]),data[:20],data[-20:]
			sd = data.split("42/market,".encode('utf8'))
			if len(sd) >= 2:
				# len(data),self.nextlen	# socket.io does wierd things with it's length==4 packets...
				content = (sd[1]).decode('utf8')
				self.onPacketReceived(content,len(content),t)
				
				self.nextlen = 0
		elif len(data) == 4:
			self.nextlen = unpack('!H',data[2:4])[0]
		elif len(data) == 3:
			if data[1] == 1 and data[2] == 51:
				reactor.callLater(self.pingInterval,self.sendPing)
				self.nextlen = 0
			else:
				self.nextlen = 0
				print("short unknown")
		else:
			self.nextlen = 0
			print("unknwon",data)
			
	
	def sendPing(self):
		self.pingcount += 1
#		print "Send Ping"
		ping = bytearray([129,1])+bytes("2".encode('utf8'))
		self.transport.write(bytes(ping))
	
	def requestMarket(self):
		sd = b"40/market,"
#		print "Send RequestMarket"
		sp = bytearray([129,len(sd)])+bytes(sd)
		self.transport.write(bytes(sp))
		
	def Terminate(self,reason):
	 	print("Terminate",reason)
		
	def onPacketReceived(self,data,length,t):
		""" Dummy, implement Your own websocket-packet-processing"""
		print("Packet",length,data)
	
	def connectionLost(self,reason):
		print("WSconnectionLost",reason)
	
class WSjsonBitcoinDEProtocol2(ClientIo2011Protocol):
	""" Processes the Content of the Websocket packet treating it as JSON and pass the dict to an onEvent-function mimmicing the original js behaviour"""
	def onPacketReceived(self,data,length,t):
		di = data.index(',')
		evt,args = data[2:di-1],loads(data[di+1:-1])
		self.factory.onEvent(evt,args,t)
		
	def Terminate(self,reason):
	 	print("WSjson Terminate",reason)

	def connectionLost(self,reason):
		print("WSjson connectionLost",reason)

# * * * * * * * * * * * MultiSource Factories * * * * * * * * * * * #

class MultiSource(Factory):

	def __init__(self,sid,receiver):
		self.sid = sid
		self.receiver = receiver
	
	def __str__(self):
		return "WSSource%d %s"%(self.sid,self.WSversion)
		
	def onEvent(self,evt,data,t):
		self.receiver.ReceiveEvent(evt,data,self.sid,t)

class BitcoinWSSourceV09(MultiSource):
	protocol = WSjsonBitcoinDEProtocol
	WSversion = "09"
	
	def __str__(self):
		return "WSSource%d %s"%(self.sid,self.WSversion)
	
	def startFactory(self):
		print("%s started"%(self))
	
	def startedConnecting(self,connector):
		print("%s connected %d"%(self,connected))
	
	def Lost(self):
		print("\t%s client called lost"%self)
	
	def connectionLost(self,connector,reason):
		print("\t%s connectionList"%self,connector,reason)
	
class BitcoinWSSourceV20(BitcoinWSSourceV09):
	protocol = WSjsonBitcoinDEProtocol2
	WSversion = "20"

# * * * * * * * * * * MultiSource Implementation * * * * * * * * * * #

class Event(object):
	def __init__(self,eventID,eventType):
		self.eventID = eventID
		self.eventType = eventType
		
		self.sources = []
		self.eventData = {}
		
	def AddSource(self,at,src):
		self.sources.append((at,src,))
		
	def AddData(self,data):
		self.eventData = data
	
	def Since(self):
		if len(self.sources) == 0:
			return 0,0,""
		else:
			self.sources = sorted(self.sources,key=lambda x : x[0])
			srcs = [x[1] for x in self.sources]
			return self.sources[0][0],self.sources[-1][0]-self.sources[0][0],srcs
			
	def __str__(self):
		return "Event %s %s %s"%(self.eventType,self.eventID,self.eventData)

class bitcoinWSeventstream(object):
	def __init__(self,stream):
		self.stream = stream
		self.checktask = task.LoopingCall(self.Cleanup)
		self.interval = 60
		self.checktask.start(self.interval,False)
		self.events = {}
	
	def Cleanup(self):
		now = time()
		events = {}
		n,m,dt,ll,srcl = 0,0,[1000,0,0],{},{}
		for k,v in self.events.items():
			s,d,srcs = v.Since()
			if s >= now-self.interval:
				events[k] = v
				n += 1
			else:
				m += 1
				dt[0] = min(dt[0],d)
				dt[1] += d
				dt[2] = max(dt[2],d)
				
				src,l = srcs[0],len(srcs)
				srcl[src] = srcl.get(src,0)+1
				ll[l] = ll.get(l,0)+1
				
		if m > 0:
			dt[1] = dt[1]/(1.*m)
		else:
			dt[0] = 0
				
		self.events = events
		print("Cleanup",self.stream,n,m,map(lambda x : "%.6f"%x,dt),ll,srcl)
	
	def ProcessEvent(self,data,src,t):
		eventID = self.GenerateID(data)
		
		is_new = False
		evt = self.events.get(eventID,None)
		if evt == None:
			evt = Event(eventID,self.stream)
			evt.AddData(self.RetrieveData(data))
			self.events[eventID] = evt
			is_new = True
		evt.AddSource(t,src)
		
		if is_new:
			return evt
		else:
			return None
	
	def RetrieveData(self,data):
		return data
		
	
class bitcoinWSremoveOrder(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSremoveOrder,self).__init__("rm")
	
	def GenerateID(self,data):
		return data['id']
		
class Countrys(object):
	def __init__(self):
		self.codes = ["DE","AT","CH","BE","GR","MT","SI","BG","IE","NL","SK","DK","IT","ES","HR","PL","CZ","EE","LV","PT","HU","FI","LT","RO","GB","FR","LU","SE","CY","IS","LI","NO","MQ"]
	
	def decode(self,u):
		pass
		
		
	def encode(self,codes):
		i,j = 0,0
		
	
class bitcoinWSaddOrder(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSaddOrder,self).__init__("add")
		
		self.countries = Countrys()
		
		self.trans = {}
		self.trans["uid"] = ("uid",lambda x :x)
		self.trans["order_id"] = ("oid",lambda x : x)
		self.trans["id"] = ("DEid",lambda x : int(x))
		self.trans["price"] = ("price",lambda x : int(float(x)*100))
		self.trans["trading_pair"] = ("pair",lambda x : x)
		self.trans["bic_full"] = ("cBIC",lambda x : x)
		self.trans["only_kyc_full"] = ("rkyc",lambda x : int(x))
		self.trans["is_kyc_full"] = ("ukyc",lambda x : int(x))
		self.trans["amount"] = ("amt",lambda x : float(x))
		self.trans["min_amount"] = ("mamt",lambda x : float(x))
		self.trans["order_type"] = ("type",lambda x : x)
		self.trans["order"] = ("order",lambda x : x)
#		self.trans["is_shorting"]
#		self.trans["is_shorting_allowed"] 
		self.trans["min_trust_level"] = ("trust",lambda x : {"bronze":1,"silver":2,"gold":3,"platinum":4}.get(x,0), )
		self.trans["seat_of_bank_of_creator"] = ("seat",lambda x : x)
		self.trans["trade_to_sepa_country"] = ("country",lambda x : x)
		self.trans["fidor_account"] = ("fidor",lambda x : int(x))
		
		
	def GenerateID(self,data):
		return data['id']
	
#	{u'price_it': u'\u20ac\xa0125,11', u'uid': u'0yybQZ6LpA0ifFWJrsg.', u'country_payment_method_es': u'Alemania', u'min_amount_es': u'0,487571', u'seat_of_bank_of_creator': u'de', u'min_amount_en': u'0.487571', u'country_payment_method_en': u'Germany', u'min_amount_it': u'0,487571', u'id': u'40965708', u'price_es': u'\u20ac\xa0125,11', u'price_formatted_fr': u'125,11\xa0', u'bic_short': u'0yzjPeTwlzkig1B-', u'min_amount_de': u'0,487571', u'volume_fr': u'62,56\xa0\u20ac', u'trading_pair': u'bcheur', u'amount_de': u'0,5', u'amount_it': u'0,5', u'trade_to_sepa_country': u'["DE","AT","CH","BE","GR","MT","SI","BG","IE","NL","SK","DK","IT","ES","HR","PL","CZ","EE","LV","PT","HU","FI","LT","RO","GB","FR","LU","SE","CY","IS","LI","NO","MQ"]', u'volume_de': u'62,56\xa0\u20ac', u'amount_fr': u'0,5', u'country_payment_method_it': u'Germania', u'country_payment_method_de': u'Deutschland', u'type': u'order', u'price_formatted_de': u'125,11\xa0', u'price_en': u'\u20ac125.11', u'payment_option': u'1', u'is_trade_by_fidor_reservation_allowed': u'1', u'bic_full': u'0yzjPeTw33DssfxIB4io2Mow-w..', u'volume_it': u'\u20ac\xa062,56', u'order_id': u'QN933Q', u'price': u'125.11', u'price_formatted_it': u'\xa0125,11', u'min_amount': u'0.487571', u'is_shorting_allowed': u'0', u'volume': u'62.555', u'min_amount_fr': u'0,487571', u'order_type': u'buy', u'is_kyc_full': u'1', u'is_shorting': u'0', u'amount_en': u'0.5', u'is_trade_by_sepa_allowed': u'0', u'fidor_account': u'0', u'price_fr': u'125,11\xa0\u20ac', u'price_formatted_es': u'\xa0125,11', u'volume_es': u'\u20ac\xa062,56', u'min_trust_level': u'bronze', u'volume_en': u'\u20ac62.56', u'amount': u'0.5', u'country_payment_method_fr': u'Allemagne', u'price_formatted_en': u'125.11', u'price_de': u'125,11\xa0\u20ac', u'only_kyc_full': u'1', u'amount_es': u'0,5'}
	
	
	def RetrieveData(self,data):
		short = int(data["is_shorting"])*2+int(data["is_shorting_allowed"])
		
		fidor = int(data["is_trade_by_fidor_reservation_allowed"])
		sepa = int(data["is_trade_by_sepa_allowed"])
		po = int(data["payment_option"])
		r = {"po":po,"short":short}
		
		print(fidor,sepa,po,short)
		for k,v in self.trans.items():
			t,f, = v
			r[t] = f(data.get(k))
			
		return r
		
class bitcoinWSskn(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSskn,self).__init__("skn")
		
	def GenerateID(self,data):
		return data['uid']

class bitcoinWSspr(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSspr,self).__init__("spr")
		
	def GenerateID(self,data):
		return data['uid']

class bitcoinWSrpo(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSrpo,self).__init__("po")
		
	def GenerateID(self,data):
		h,j = 0,1
		for k,v in data.items():
			m = (int(v.get("is_trade_by_fidor_reservation_allowed","0"))*2-1)
			h += int(k)*m*j
			j += 1
		return h
		
	def RetrieveData(self,data):
		pos = {}
		for k,v in data.items():
			fidor = int(v.get("is_trade_by_fidor_reservation_allowed","0"))
			sepa = int(v.get("u'is_trade_by_sepa_allowed","0"))
			po = fidor+sepa*2
			pos[int(k)] = po
		return pos

class BitcoinWSmulti(object):
	def __init__(self,servers=[1,2,3,4]):
		self.servers = {1:("ws",BitcoinWSSourceV09,),2:("ws1",BitcoinWSSourceV09,),3:("ws2",BitcoinWSSourceV20,),4:("ws3",BitcoinWSSourceV20,)}
	#	self.servers = {1:("ws1",BitcoinWSSourceV09,)}
	#	self.servers = {3:["ws2",BitcoinWSSourceV20,]}
		self.sources = {}
		self.connService = {}
		
		self.streams = {"remove_order":bitcoinWSremoveOrder(),"add_order":bitcoinWSaddOrder()}
		self.streams["skn"] = bitcoinWSskn()
		self.streams["spr"] = bitcoinWSspr()
		self.streams["refresh_express_option"] = bitcoinWSrpo()
		
		for sid in servers:
			addr,pfactory, = self.servers.get(sid,(None,None,))
			if addr != None:
				tlsctx = optionsForClientTLS(u'%s.bitcoin.de'%addr,None)
				endpoint = endpoints.SSL4ClientEndpoint(reactor, '%s.bitcoin.de'%addr, 443,tlsctx)
				self.sources[sid] = pfactory(sid,self)	# <-- Reference to self is passed here, ReceiveEvent is called by source
				self.connService[sid] = ClientService(endpoint,self.sources[sid])
				self.connService[sid].startService()
	
	def ReceiveEvent(self,evt,data,src,t):
		# Called by source, dispatches to event Stream
		t2 = time()
		stream = self.streams.get(evt,None)
		evt = None
		if stream != None:
			evt = stream.ProcessEvent(data,src,t)
		else:
			
			print("Event",src,evt,data,t2-t)
			
		if evt != None:
			self.Deliver(evt)
			
	def Deliver(self,evt):
		print(evt)
		
	def Stats(self):
		pass
	

class BitcoinDESubscribe(BitcoinWSmulti):
	funcs = {"add":[],"rm":[],"po":[],"skn":[],"spr":[]}
	
	def Deliver(self,evt):
		tpy = evt.eventType
		for f in self.funcs[tpy]:
			f(evt)
	
	def SubscribeAdd(self,func):
		self.funcs["add"].append(func)
		
	def SubscribeRemove(self,func):
		self.funcs["rm"].append(func)
		
	def SubscribeManagement(self,func):
		self.funcs["skn"].append(func)
		self.funcs["spr"].append(func)
		
	def SubscribeUpdate(self,func):
		self.funcs["po"].append(func)
	
# * * * * * * * * * * * * * * main * * * * * * * * * * * * * * #

def main():
	sources = BitcoinWSmulti()
	
	reactor.run()

# po often comes before add Event --> po caching
#Event add 50000000 {'short': 0, 'uid': u'0yybQpgLpTshf1Hhrw..', 'fidor': 0, 'ukyc': 1, 'country': u'["DE","AT","CH","BE","GR","MT","SI","BG","IE","NL","SK","DK","IT","ES","HR","PL","CZ","EE","LV","PT","HU","FI","LT","RO","GB","FR","LU","SE","CY","IS","LI","NO","MQ"]', 'price': 465050, 'oid': u'AZD24R', 'seat': u'de', 'rkyc': 1, 'mamt': 0.012902, 'order': None, 'DEid': 50000000, 'pair': u'btceur', 'trust': 1, 'type': u'sell', 'po': 3, 'amt': 0.12, 'cBIC': u'0yzjuOJz3HDsNGDQnomo2yA3kQ..'}

if __name__ == '__main__':
	main()
