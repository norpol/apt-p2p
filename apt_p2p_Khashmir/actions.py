## Copyright 2002-2004 Andrew Loewenstern, All Rights Reserved
# see LICENSE.txt for license information

"""Details of how to perform actions on remote peers."""

from twisted.internet import reactor
from twisted.python import log

from khash import intify
from util import uncompact

class ActionBase:
    """Base class for some long running asynchronous proccesses like finding nodes or values.
    
    @type caller: L{khashmir.Khashmir}
    @ivar caller: the DHT instance that is performing the action
    @type target: C{string}
    @ivar target: the target of the action, usually a DHT key
    @type config: C{dictionary}
    @ivar config: the configuration variables for the DHT
    @type action: C{string}
    @ivar action: the name of the action to call on remote nodes
    @type num: C{long}
    @ivar num: the target key in integer form
    @type queried: C{dictionary}
    @ivar queried: the nodes that have been queried for this action,
        keys are node IDs, values are the node itself
    @type answered: C{dictionary}
    @ivar answered: the nodes that have answered the queries
    @type found: C{dictionary}
    @ivar found: nodes that have been found so far by the action
    @type sorted_nodes: C{list} of L{node.Node}
    @ivar sorted_nodes: a sorted list of nodes by there proximity to the key
    @type results: C{dictionary}
    @ivar results: keys are the results found so far by the action
    @type desired_results: C{int}
    @ivar desired_results: the minimum number of results that are needed
        before the action should stop
    @type callback: C{method}
    @ivar callback: the method to call with the results
    @type outstanding: C{int}
    @ivar outstanding: the number of requests currently outstanding
    @type outstanding_results: C{int}
    @ivar outstanding_results: the number of results that are expected from
        the requests that are currently outstanding
    @type finished: C{boolean}
    @ivar finished: whether the action is done
    @type sort: C{method}
    @ivar sort: used to sort nodes by their proximity to the target
    """
    
    def __init__(self, caller, target, callback, config, stats, action, num_results = None):
        """Initialize the action.
        
        @type caller: L{khashmir.Khashmir}
        @param caller: the DHT instance that is performing the action
        @type target: C{string}
        @param target: the target of the action, usually a DHT key
        @type callback: C{method}
        @param callback: the method to call with the results
        @type config: C{dictionary}
        @param config: the configuration variables for the DHT
        @type stats: L{stats.StatsLogger}
        @param stats: the statistics gatherer
        @type action: C{string}
        @param action: the name of the action to call on remote nodes
        @type num_results: C{int}
        @param num_results: the minimum number of results that are needed before
            the action should stop (optional, defaults to getting all the results)
        
        """
        
        self.caller = caller
        self.target = target
        self.config = config
        self.action = action
        stats.startedAction(action)
        self.num = intify(target)
        self.queried = {}
        self.answered = {}
        self.found = {}
        self.sorted_nodes = []
        self.results = {}
        self.desired_results = num_results
        self.callback = callback
        self.outstanding = 0
        self.outstanding_results = 0
        self.finished = False
    
        def sort(a, b, num=self.num):
            """Sort nodes relative to the ID we are looking for."""
            x, y = num ^ a.num, num ^ b.num
            if x > y:
                return 1
            elif x < y:
                return -1
            return 0
        self.sort = sort

    #{ Main operation
    def goWithNodes(self, nodes):
        """Start the action's process with a list of nodes to contact."""
        for node in nodes:
            if node.id == self.caller.node.id:
                continue
            else:
                self.found[node.id] = node
        self.sortNodes()
        self.schedule()
    
    def schedule(self):
        """Schedule requests to be sent to remote nodes."""
        # Check if we are already done
        if self.desired_results and ((len(self.results) >= abs(self.desired_results)) or
                                     (self.desired_results < 0 and
                                      len(self.answered) >= self.config['STORE_REDUNDANCY'])):
            self.finished = True
            result = self.generateResult()
            reactor.callLater(0, self.callback, *result)

        if self.finished or (self.desired_results and 
                             len(self.results) + self.outstanding_results >= abs(self.desired_results)):
            return
        
        # Loop for each node that should be processed
        for node in self.getNodesToProcess():
            # Don't send requests twice or to ourself
            if node.id not in self.queried and node.id != self.caller.node.id:
                self.queried[node.id] = 1
                
                # Get the action to call on the node
                try:
                    f = getattr(node, self.action)
                except AttributeError:
                    log.msg("%s doesn't have a %s method!" % (node, self.action))
                else:
                    # Get the arguments to the action's method
                    try:
                        args, expected_results = self.generateArgs(node)
                    except ValueError:
                        pass
                    else:
                        # Call the action on the remote node
                        self.outstanding += 1
                        self.outstanding_results += expected_results
                        df = f(self.caller.node.id, *args)
                        df.addCallbacks(self.gotResponse, self.actionFailed,
                                        callbackArgs = (node, expected_results),
                                        errbackArgs = (node, expected_results))
                        
            # We might have to stop for now
            if (self.outstanding >= self.config['CONCURRENT_REQS'] or
                (self.desired_results and
                 len(self.results) + self.outstanding_results >= abs(self.desired_results))):
                break
            
        assert self.outstanding >= 0
        assert self.outstanding_results >= 0

        # If no requests are outstanding, then we are done
        if self.outstanding == 0:
            self.finished = True
            result = self.generateResult()
            reactor.callLater(0, self.callback, *result)

    def gotResponse(self, dict, node, expected_results):
        """Receive a response from a remote node."""
        self.caller.insertNode(node)
        if self.finished or self.answered.has_key(node.id):
            # a day late and a dollar short
            return
        self.outstanding -= 1
        self.outstanding_results -= expected_results
        self.answered[node.id] = 1
        self.processResponse(dict['rsp'])
        self.schedule()

    def actionFailed(self, err, node, expected_results):
        """Receive an error from a remote node."""
        log.msg("action %s failed (%s) %s/%s" % (self.action, self.config['PORT'], node.host, node.port))
        log.err(err)
        self.caller.table.nodeFailed(node)
        self.outstanding -= 1
        self.outstanding_results -= expected_results
        self.schedule()
    
    def handleGotNodes(self, nodes):
        """Process any received node contact info in the response.
        
        Not called by default, but suitable for being called by
        L{processResponse} in a recursive node search.
        """
        for compact_node in nodes:
            node_contact = uncompact(compact_node)
            node = self.caller.Node(node_contact)
            if not self.found.has_key(node.id):
                self.found[node.id] = node

    def sortNodes(self):
        """Sort the nodes, if necessary.
        
        Assumes nodes are never removed from the L{found} dictionary.
        """
        if len(self.sorted_nodes) != len(self.found):
            self.sorted_nodes = self.found.values()
            self.sorted_nodes.sort(self.sort)
                
    #{ Subclass for specific actions
    def getNodesToProcess(self):
        """Generate a list of nodes to process next.
        
        This implementation is suitable for a recurring search over all nodes.
        """
        self.sortNodes()
        return self.sorted_nodes[:self.config['K']]
    
    def generateArgs(self, node):
        """Generate the arguments to the node's action.
        
        These arguments will be appended to our node ID when calling the action.
        Also return the number of results expected from this request.
        
        @raise ValueError: if the node should not be queried
        """
        return (self.target, ), 0
    
    def processResponse(self, dict):
        """Process the response dictionary received from the remote node."""
        self.handleGotNodes(dict['nodes'])

    def generateResult(self, nodes):
        """Create the final result to return to the L{callback} function."""
        return []
        

class FindNode(ActionBase):
    """Find the closest nodes to the key."""

    def __init__(self, caller, target, callback, config, stats, action="findNode"):
        ActionBase.__init__(self, caller, target, callback, config, stats, action)

    def processResponse(self, dict):
        """Save the token received from each node."""
        if dict["id"] in self.found:
            self.found[dict["id"]].updateToken(dict.get('token', ''))
        self.handleGotNodes(dict['nodes'])

    def generateResult(self):
        """Result is the K closest nodes to the target."""
        self.sortNodes()
        return (self.sorted_nodes[:self.config['K']], )
    

class FindValue(ActionBase):
    """Find the closest nodes to the key and check for values."""

    def __init__(self, caller, target, callback, config, stats, action="findValue"):
        ActionBase.__init__(self, caller, target, callback, config, stats, action)

    def processResponse(self, dict):
        """Save the number of values each node has."""
        if dict["id"] in self.found:
            self.found[dict["id"]].updateNumValues(dict.get('num', 0))
        self.handleGotNodes(dict['nodes'])
        
    def generateResult(self):
        """Result is the nodes that have values, sorted by proximity to the key."""
        self.sortNodes()
        return ([node for node in self.sorted_nodes if node.num_values > 0], )
    

class GetValue(ActionBase):
    """Retrieve values from a list of nodes."""
    
    def __init__(self, caller, target, local_results, num_results, callback, config, stats, action="getValue"):
        """Initialize the action with the locally available results.
        
        @type local_results: C{list} of C{string}
        @param local_results: the values that were available in this node
        """
        ActionBase.__init__(self, caller, target, callback, config, stats, action, num_results)
        if local_results:
            for result in local_results:
                self.results[result] = 1

    def getNodesToProcess(self):
        """Nodes are never added, always return the same sorted node list."""
        return self.sorted_nodes
    
    def generateArgs(self, node):
        """Arguments include the number of values to request."""
        if node.num_values > 0:
            # Request all desired results from each node, just to be sure.
            num_values = abs(self.desired_results) - len(self.results)
            assert num_values > 0
            if num_values > node.num_values:
                num_values = 0
            return (self.target, num_values), node.num_values
        else:
            raise ValueError, "Don't try and get values from this node because it doesn't have any"

    def processResponse(self, dict):
        """Save the returned values, calling the L{callback} each time there are new ones."""
        if dict.has_key('values'):
            def x(y, z=self.results):
                if not z.has_key(y):
                    z[y] = 1
                    return y
                else:
                    return None
            z = len(dict['values'])
            v = filter(None, map(x, dict['values']))
            if len(v):
                reactor.callLater(0, self.callback, self.target, v)

    def generateResult(self):
        """Results have all been returned, now send the empty list to end the action."""
        return (self.target, [])
        

class StoreValue(ActionBase):
    """Store a value in a list of nodes."""

    def __init__(self, caller, target, value, num_results, callback, config, stats, action="storeValue"):
        """Initialize the action with the value to store.
        
        @type value: C{string}
        @param value: the value to store in the nodes
        """
        ActionBase.__init__(self, caller, target, callback, config, stats, action, num_results)
        self.value = value
        
    def getNodesToProcess(self):
        """Nodes are never added, always return the same sorted list."""
        return self.sorted_nodes

    def generateArgs(self, node):
        """Args include the value to store and the node's token."""
        if node.token:
            return (self.target, self.value, node.token), 1
        else:
            raise ValueError, "Don't store at this node since we don't know it's token"

    def processResponse(self, dict):
        """Save the response, though it should be nothin but the ID."""
        self.results[dict["id"]] = dict
    
    def generateResult(self):
        """Return all the response IDs received."""
        return (self.target, self.value, self.results.values())
