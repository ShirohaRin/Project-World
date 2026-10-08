import os
import urllib.request

url = "http://127.0.0.1:8080/api/admin/rebuild?collection=novel"
request = urllib.request.Request(
    url,
    method="POST",
    headers={"X-API-Key": os.environ["RAG_ADMIN_KEY"]},
)
print(urllib.request.urlopen(request, timeout=600).read().decode())
