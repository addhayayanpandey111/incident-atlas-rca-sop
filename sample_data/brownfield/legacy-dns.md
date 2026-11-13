# DNS lookup outage notes
## Impact
Checkout requests failed for six minutes due to DNS resolution timeouts.
## Root cause
The checkout DNS resolver configuration pointed to a retired nameserver after a node image update.
## Prevention
Validate nameserver configuration in the node image release checks.
