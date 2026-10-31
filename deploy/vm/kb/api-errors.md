# Common API errors

- 401 Unauthorized: the API key is missing, revoked or sent in the wrong header. Send it as a Bearer token.
- 403 Forbidden: the key is valid but lacks the permission for this endpoint. Check the key's scopes.
- 409 Conflict: the request clashes with the current state of the resource, for example creating something
  that already exists. Read the resource first, then retry with the current version.
- 429 Too Many Requests: you are over the rate limit. Wait for the number of seconds in the Retry-After
  header, then retry with backoff.
- 5xx errors: a problem on our side. Retry with exponential backoff and check the status page.
