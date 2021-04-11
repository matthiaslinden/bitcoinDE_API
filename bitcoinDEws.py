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

from enum import Enum
from time import time
from hashlib import sha1
from json import loads

import re

from os import urandom
from base64 import b64encode,urlsafe_b64encode	# Websocket Key handling
from struct import pack,unpack	# Websocket Length handling

from twisted.python import log

from twisted.internet import endpoints,task,reactor
from twisted.internet.ssl import optionsForClientTLS
from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.protocol import Protocol, Factory
from twisted.application.internet import ClientService
from twisted.protocols import basic

# Retiered socket.io v0.9.16 and v2.0.11 versions, as bitcoin.de doesn't supply them any more.

# * * * * * * * * * * * socket.io > 2.4 Implementation * * * * * * * * * * * #

class ClientIo24State(Enum):
	init = 0
	sid = 1
	upgrade = 2
	websocket = 3
	market = 4

class ClientIo24Protocol(basic.LineReceiver):
	_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"	# Handshake key signing
	
	def connectionMade(self):
		self.nonce = int(time()*1000)
		self.sid = None
		self.header = [{"lines":[]}]
		self.eio = 3
		
		self.pingInterval = 20
		self.pingcount = 0
		self.ws_packet_count = 0
		
		self.state = ClientIo24State.init
		self.sendInit()
		
	def GetNonce(self):
		self.nonce += 1
		return urlsafe_b64encode(pack('>q',self.nonce)).decode("utf-8")[:-1]
	
	def sendInit(self):
		self.state = ClientIo24State.init
		self.setLineMode()
		data = "GET /socket.io/1/?EIO=%d&transport=polling&t=%s HTTP/1.1\r\n"%(self.eio,self.GetNonce())
		self.sendLine(data.encode('utf8'))	# first GET reques
	
	def lineReceived(self,line):
		if len(line) > 3:
			self.header[-1]["lines"].append(line)
		else:
			self.onHTTPHeader()
	
	def ParseSID_Packet(self,line):
		try:
			sid = loads("{"+line.split("{")[1].split("}")[0]+"}")
			self.sid = sid.get("sid",None)
			self.pingInterval = sid.get("pingInterval",1100*20)/1100
		except:
			sid = {}
		return sid
	
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
			elif "Upgrade:" in line:
				header["Upgrade"] = line.split(": ")[-1]
			elif "Sec-WebSocket-Accept" in line:
				header["Key"] = line.split(": ")[-1]
		
		if code == "101" and header.get("Upgrade",None) == "websocket":
			if not self.checkWebsocket():
				self.sendInit()
		elif code == "200":
			self.setRawMode()	
		else:
			print(code,header)
		
		self.header.append({"lines":[]})
		
	def sendUpgrade(self):
		key = b64encode(urandom(16))
		self.websocket_key = key
		
		data = ""
		data = "GET /socket.io/1/?EIO=%d&transport=websocket&t=%s&sid=%s HTTP/1.1\r\n"%(self.eio,self.GetNonce(),self.sid)
		data += "Connection: Upgrade\r\nUpgrade: Websocket\r\n"
		data += "Cache-Control: no-cache\r\n"
		data += "Pragma: no-cache\r\nCache-Control: no-cache\r\n"		
		data += "Sec-WebSocket-Key: %s\r\n"%(self.websocket_key.decode('utf8'))
		data += "Sec-WebSocket-Version: 13\r\n"
		self.sendLine(data.encode('utf8'))
		
	def checkWebsocket(self):
		key_got = self.header[-1].get("Key","")
		mysha1 = sha1()
		mysha1.update(self.websocket_key + self._MAGIC)
		key_accept = b64encode(mysha1.digest()).decode('utf8')
		if key_got == key_accept:
			self.setRawMode()
			reactor.callLater(1,self.sendPing,"probe")
			self.state = ClientIo24State.upgrade
			print("WS 2.4 connection accepted")
			return True
		return False
			
	def WebsocketSend(self,data,binary=False,op="text"):
		l = len(data)
		
		fin = 0x80
		opcode = {"text":1,"binary":2,"ping":3}.get(op,1)
		is_masked = 0x8000
		
		if binary == False:
			if l > 126:
				pass
			else:
				length = l<<8
				sdata = pack('H',fin+opcode+is_masked+length)
			sdata += pack('I',0)
			sdata += data			
			self.transport.write(sdata)
		else:
			print("binary not yet implemented",data)
		
	def rawDataReceived(self,data):
		t = time()
		if type(data) == str:
			data = bytearray(data)
			
		if self.state == ClientIo24State.init:
			pdata = self.ParseSID_Packet(data.decode("utf-8"))
			if self.sid != None:
				self.state = ClientIo24State.sid
				self.setLineMode()
				self.sendUpgrade()
		else:
			self.DecodeWebsocket(data,t)
	
	def DecodeWebsocket(self,data,t):
		self.ws_packet_count += 1
		l,b = data[1]&(0b1111111),2	# Handle the length field
		op = data[0] & 0x0f
		if l == 126:
			b = 4
			l = unpack('!H',data[2:4])[0]	# struct.unpack
		elif l == 127:
			b = 10
			l = unpack('!Q',data[2:10])[0]
		
		if op == 1:
			if self.state == ClientIo24State.websocket:
				if data[b:] == "3".encode("utf-8"):
					reactor.callLater(self.pingInterval,self.sendPing)
				else:
					self.onPacketReceived(data[b:],l-b,t)
			elif self.state == ClientIo24State.upgrade:
				if data[b:] == "3probe".encode("utf-8"):
					reactor.callLater(1,self.WebsocketSend,"5".encode("utf-8"))
					reactor.callLater(5,self.sendPing)
					reactor.callLater(1.5,self.WebsocketSend,"40/market,".encode("utf-8"))
					self.state = ClientIo24State.websocket
				else:
					print("wrong probe response",data[b:])
					
		else:
			print("other op",op)	

	def sendPing(self,text=""):
		self.pingcount += 1
		self.WebsocketSend(("2"+text).encode("utf-8"))
		
	def Terminate(self,reason):
	 	print("Terminate",reason)
		
	def onPacketReceived(self,data,length,t):
		""" Dummy, implement Your own websocket-packet-processing"""
		print("Packet",length,data,t)
	
	def connectionLost(self,reason):
		print("WSconnectionLost",reason)

class WSjsonBitcoinDEProtocol24(ClientIo24Protocol):
	""" Processes the Content of the Websocket packet treating it as JSON and pass the dict to an onEvent-function mimmicing the original js behaviour"""
	def onPacketReceived(self,data,length,t):
		try:
			content = data.decode("utf-8")
		except:
			dsplit = re.split(b'\x81~\x06',data)
			if len(dsplit) > 1:
				for d in dsplit:
					self.onPacketReceived(d,len(d),t)
			else:	# Recovery not possible, split doesn't yield two parts
				pass
		else:
			ci = content.find('[',0,20)
			if ci != -1:
				evt,args = loads(content[ci:])
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

class BitcoinWSSourceV24(MultiSource):
	protocol = WSjsonBitcoinDEProtocol24
	WSversion = "24"
	
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
	"""Handles an Eventstream, for example 'add'-Events. ProcessEvent only forwards the first occurence of an event from one of the sources. Already received events get timestamd-data via AddSource"""
	def __init__(self,stream,interval=60):
		self.stream = stream
		self.checktask = task.LoopingCall(self.Cleanup)
		self.interval = interval	# Remove old Events from stream
		self.checktask.start(self.interval,False)
		self.events = {}
	
	def Cleanup(self):
		"""Periodically removes old events from stream"""
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
		print("Cleanup",self.stream,n,m,[x for x in map(lambda x : "%.6f"%x,dt)],ll,srcl)
	
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
		
#		print(fidor,sepa,po,short)
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

class bitcoinWSlt(bitcoinWSeventstream):
	def __init__(self):
		super(bitcoinWSlt,self).__init__("lt")
		
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
	"""ClientService ensures restart after connection is lost."""
	def __init__(self,servers=[1,2,3,4]):
		self.servers = {1:("ws",BitcoinWSSourceV24,),2:("ws1",BitcoinWSSourceV24,),3:("ws2",BitcoinWSSourceV24,),4:("ws3",BitcoinWSSourceV24,)}
	#	self.servers = {1:("ws1",BitcoinWSSourceV09,)}
	#	self.servers = {3:["ws2",BitcoinWSSourceV20,]}
		self.sources = {}
		self.connService = {}
		
		self.streams = {"remove_order":bitcoinWSremoveOrder(),"add_order":bitcoinWSaddOrder()}
		self.streams["skn"] = bitcoinWSskn()
		self.streams["spr"] = bitcoinWSspr()
		self.streams["lt"] = bitcoinWSlt()
		self.streams["refresh_express_option"] = bitcoinWSrpo()
		
		for sid in servers:
			addr,pfactory, = self.servers.get(sid,(None,None,))
			if addr != None:
				tlsctx = optionsForClientTLS(u'%s.bitcoin.de'%addr,None)
				endpoint = endpoints.SSL4ClientEndpoint(reactor, '%s.bitcoin.de'%addr, 443,tlsctx)
				self.sources[sid] = pfactory(sid,self)	# <-- Reference to self is passed here, ReceiveEvent is called by source
				self.connService[sid] = ClientService(endpoint,self.sources[sid])
				self.connService[sid].startService()
	
	def ReceiveEvent(self,devt,data,src,t):
		# Called by source, dispatches to event Stream
		t2 = time()
		stream = self.streams.get(devt,None)
		evt = None
		if stream != None:
			evt = stream.ProcessEvent(data,src,t)
		else:
			print("no Event stream for",src,devt,data,t2-t)
			
		if evt != None:
			self.Deliver(evt)
			
	def Deliver(self,evt):
		pass
#		print(evt)
		
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
