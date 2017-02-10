#!/usr/bin/env python2.7
#coding:utf-8

import time

from bitcoinDEapi import *

from twisted.internet.defer import Deferred,setDebugging

t1 = time.time()

# Function to display the result along with a timestamp
def ready(result):
	print time.time()-t1,result
	return result

def main():
	
	# Replace with your own credentials
	api_secret = ""
	api_key = ""
	
	api = BitcoinDeAPI(reactor,api_key,api_secret)
	setDebugging(True)
	
	# Some ordinary requests
	api.APIRequest("showOrderbook",type="sell").addCallback(ready)
	api.APIRequest("showMyOrders",type="sell").addCallback(ready)
# 	api.APIRequest("showMyTrades",type="sell").addCallback(ready)
# 	api.APIRequest("showAccountInfo").addCallback(ready)
# 	api.APIRequest("showAccountLedger",type='kickback').addCallback(ready)

	# delayed call to 'showAccountLedger' to demonstrate that multiple requests to the same command are executed as one request
 	def later():
 		api.APIRequest("showAccountLedger",type='kickback').addCallback(ready)


	reactor.callLater(4,later)
	reactor.callLater(4,later)
	reactor.callLater(4,later)
	reactor.callLater(5,later)
	reactor.callLater(6,later)
	reactor.callLater(15,later)
	
	#	for i in range(10):
	#		api.APIRequest("showAccountLedger",type='kickback').addCallback(ready)
		#	api.APIRequest("showMyTrades",type="sell",state="1",page="%d"%(30+3)).addCallback(ready)
	#		time.sleep(0.01)
	
	
	reactor.run()

if __name__ == "__main__":
	main()
