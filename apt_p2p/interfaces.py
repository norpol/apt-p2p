
"""Some interfaces that are used by the apt-p2p classes."""

from zope.interface import Interface

class IDHT(Interface):
    """An abstract interface for using a DHT implementation."""
    
    def loadConfig(self, config, section):
        """Load the DHTs configuration from a dictionary.
        
        @type config: C{SafeConfigParser}
        @param config: the dictionary of config values
        """
    
    def join(self):
        """Bootstrap the new DHT node into the DHT.
        
        @rtype: C{Deferred}
        @return: a deferred that will fire when the node has joined
        """
        
    def leave(self):
        """Depart gracefully from the DHT.
        
        @rtype: C{Deferred}
        @return: a deferred that will fire when the node has left
        """
        
    def getValue(self, key):
        """Get a value from the DHT for the specified key.
        
        The length of the key may be adjusted for use with the DHT.

        @rtype: C{Deferred}
        @return: a deferred that will fire with the stored values
        """
        
    def storeValue(self, key, value):
        """Store a value in the DHT for the specified key.

        The length of the key may be adjusted for use with the DHT.
        """

class IDHTStats(Interface):
    """An abstract interface for DHTs that support statistics gathering."""
    
    def getStats(self):
        """Gather and format all the statistics for the DHT.
        
        The statistics will be formatted for inclusion in the body
        of an HTML page.
        
        @rtype: C{string}
        @return: the formatted statistics, suitable for displaying to the user
        """
    
class IDHTStatsFactory(Interface):
    """An abstract interface for DHTs that support statistics displaying."""
    
    def getStatsFactory(self):
        """Create and return an HTTP factory for displaying statistics.
        
        @rtype: 
        """
        