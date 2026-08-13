# Client Service Boundaries

- IDEA Assistant clients authenticate with IDEA account sessions only. They do not store RAG keys or call RAG storage APIs directly.
- Cloud memory is accessed only through IDEA service APIs under the logged-in account and project permissions.
- TRAE and other MCP automation clients use an issued device credential. The same credential can authenticate the IDEA Owner MCP and the RAG Owner Admin proxy.
- RAG authorizes project requests by asking IDEA to validate the presented identity and project permission.
- New client features must reuse these interfaces. Do not introduce static RAG keys, client-side database access, or parallel memory authorization paths.
