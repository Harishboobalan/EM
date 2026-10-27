# Event Mesh → CPI → Python DCD receiver (Cloud Foundry)

```
Event Mesh queue          CPI iFlow                                                    SAP BTP Cloud Foundry
s4-queue-HACKATHON ──► AMQP sender ──► Content Modifier ──► Request Reply ──HTTP──►  em-webhook (receiver.py)
                                                                                     POST /dcd/events
```

**Endpoint:** `https://em-webhook-mediating-fossa-mx.cfapps.us10.hana.ondemand.com/dcd/events`

---

## 1. Python receiver (`receiver.py`)

`POST /dcd/events`:

| Step | Behaviour |
|---|---|
| Auth | None (`REQUIRE_AUTH=false` in `manifest.yml`). Set it to `true` to require Basic auth (`RECEIVER_USER` / `RECEIVER_PASSWORD`) or header `X-API-Key` (`RECEIVER_API_KEY`), else **401** |
| Body | JSON is parsed; any other body (e.g. plain text) is passed on as a string |
| Event ID | Header `X-Event-Id`, else CloudEvents `id` in the body, else SHA-256 of the body |
| Duplicates | Already processed IDs (SQLite `events.db`) are skipped with **200** |
| Processing | `process_event()`, where you plug in the agent logic. Raise an exception on failure |
| Result | **200** on success, **500** on failure, which makes CPI fail the run and Event Mesh redeliver |

`ASYNC_PROCESSING=true`: the event is stored in SQLite and acknowledged with 200 at once. A background
thread processes it and retries failures. Once 200 is returned, the local store owns the event.
On Cloud Foundry the disk is ephemeral (lost on restart/restage), so for async mode in production
use a bound database instead of `events.db`.

### Run locally

```powershell
.venv\Scripts\pip install -r requirements.txt python-dotenv
.venv\Scripts\python receiver.py          # reads .env, listens on RECEIVER_PORT (8000)
curl.exe -X POST http://localhost:8000/dcd/events -H "Content-Type: application/json" -d '{\"id\": \"t1\", \"data\": {}}'
```

## 2. Deploy to Cloud Foundry (Option A)

```powershell
cd D:\Sierra\EM
cf push
cf logs em-webhook          # watch events arrive
```

`manifest.yml` runs `gunicorn` with **one** worker so the dedup store and async worker are shared.
The URL stays the same on redeploys.

## 3. CPI iFlow

**3a. Security material.** The receiver needs no credentials. Check that the `EventMesh_OAuth` OAuth2 credential uses the `s4_client` values from `.env`.

**AMQP sender adapter**

| Field | Value |
|---|---|
| Transport / Message Protocol | WebSocket / AMQP 1.0 |
| Host | `bms-isd-9555164b-ab69-4396-8737-d15e71a46e92.us10.a.eventmesh.integration.cloud.sap` |
| Port / Path | `443` / `/protocols/amqp10ws` (**no** queue name in the path) |
| Authentication | OAuth2 Client Credentials, `EventMesh_OAuth` |
| Queue Name | `queue:s4-queue-HACKATHON` |

**3b. Content Modifier** (message headers):

| Name | Source | Value |
|---|---|---|
| `Content-Type` | Constant | `application/json` |
| `X-Event-Id` | Expression | `${header.ce_id}` or `${header.message-id}`, depending on what the AMQP headers carry |

**3c. Request Reply → Receiver (HTTP adapter)**

| Field | Value |
|---|---|
| Address | `https://em-webhook-mediating-fossa-mx.cfapps.us10.hana.ondemand.com/dcd/events` |
| Proxy Type | Internet |
| Method | POST |
| Authentication | None |
| Request Headers | `Content-Type\|X-Event-Id` |
| Timeout | e.g. 120000 ms |
| Throw Exception on Failure | **on** |

**3d. Exception Subprocess** (optional): log/alert, then end with an **Error End** so the message is
released for retry.
**3e.** Save → Deploy → Manage Integration Content shows **Started**.

> Only one consumer may read `s4-queue-HACKATHON`. Undeploy other iFlows on the same queue.

## 4. Test end to end

1. `cf logs em-webhook` open, `GET /` answers.
2. Publish a test message to the topic/queue (Event Mesh UI).
3. CPI Monitor Message Processing shows **Completed**, and the log shows `Processing event ...`.
4. Failure test: `cf stop em-webhook`, publish again. The CPI run fails and the message stays in or
   returns to the queue. `cf start em-webhook`, and it is delivered on the next retry.

## Troubleshooting

| Symptom | Check |
|---|---|
| No iFlow runs at all | Queue subscribed to the S/4 topic? S/4 `/IWXBE/EVENT_MONITOR`? Another consumer? |
| HTTP 401 in CPI | `REQUIRE_AUTH` must be `false` (`cf env em-webhook`) |
| HTTP 500 in CPI | `cf logs em-webhook --recent`, the agent raised an exception |
| AMQP adapter error | Host/Path, `queue:` prefix, `EventMesh_OAuth` client/secret/token URL |
