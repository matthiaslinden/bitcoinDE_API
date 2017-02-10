# bitcoinDE_API
Collection of functions to use bitcoin.de API in python, using twisted as asynchronous framework.

## API
* Tested in python2.7
* Implements all API-calls


## Classes
* BitcoinDeAPI: Encapsulates the basic encryption handling nesseccary to communicate with the bitcoin.de API. Hooks for different error and success handlers are build in, following the async model of twisted.
* BitcoinDeAPINonce: Adds an Error Handler in case the nonce is wrong. Tries to reset it to a sensible value
* QueuedBitcoinDeAPI: Adds queueing support for requests to reflect bitcoin.de's credit system
* PriorityBitcoinDeAPI: Adds priority awareness for requests

### Queueing
As the bitcoin.de API restricts spamming using a credit-system, with requests costing between 2 and 3 credits each, a queueing system is added that tracks available credits and throttles requests accordingly. In case a retryperiode is returned, no requests are send out until the system has recovered.
Requests are not send out back-to-back, but with a .2s gap. Otherwise bitcoin.de's nonce system might get confused, as proper order of delivery doesn't seem to be guaranteed.
Connection pooling helps with this issue, otherwise a .4s gap has to be applied.

If the same request is already enqueued, waiting for execution, it doesn't get enqueued, but the callback is added to the first request's callback chain.

### Responses
In addition the JSON dict containing the response-body, the following fields are added:
{code': 200, u'credits': 18, u'errors': [], 'phrase': 'OK'}

## socket.io
bitoin.de supplies a 'websocket' interface to inform in realtime about changes to the marketplace. It's based on socket.io (version 09.16) and doesn't provide simple websocket connectivity.

### Procedure
* https Get request to base address returns connection-options (websocket,longpolling,...)
* Switch to desired Protocol using the same https connection
* Receive websocket packages, send heartbeat to keep connection open

### Implementation
bitcoinDEws implementation uses twisted basic.LineReceiver to make the GET request to the base-address, some crude line-magic to process the response and initiate the protocol-switching. No real websocket implementation is used, but a line-oriented bare minimum.

* ClientIo0916Protocol encapsulates the basic connection handling
* WSjsonBitcoinDEProtocol adds JSON data handling
* BitcoinDEProtocol adds awareness to the three types of events communicated and the relevant data

BitcoinDESubscribeFactory and BitcoinDESubscribe add callback-subscriptions to make data available in an async application