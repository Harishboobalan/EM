# Event Mesh → CPI → Python webhook (Cloud Foundry)

```
Event Mesh queue            CPI iFlow                              SAP BTP Cloud Foundry
s4-queue-HACKATHON  ──►  AMQP sender adapter  ──►  HTTP receiver  ──►  em-webhook (webhook.py)
                        (consumes the message)      adapter (POST)      prints it -> cf logs
```

CPI consumes the message from Event Mesh and POSTs it to the `em-webhook` app on
Cloud Foundry. The app prints every message; you watch them with `cf logs`.

**Webhook URL:** `https://em-webhook-mediating-fossa-mx.cfapps.us10.hana.ondemand.com`

---

## 1. Webhook app (Cloud Foundry)

Already deployed to org `BMS-BLD`, space `BLD`. Files:

| File | Purpose |
|---|---|
| `webhook.py` | HTTP server: prints each POSTed message, answers 200 |
| `manifest.yml` | CF app settings (name `em-webhook`, 128 MB, python buildpack) |
| `runtime.txt` | Python version for the buildpack |
| `requirements.txt` | Empty - only the standard library is used |
| `.cfignore` | Keeps `.env`, `.venv` etc. out of the upload |

### Watch the messages (live)

```powershell
cf logs em-webhook
```

Recent messages only: `cf logs em-webhook --recent`

### Redeploy after changing `webhook.py`

```powershell
cd D:\Sierra\EM
cf push
```

The URL stays the same on redeploys. Remove the app with `cf delete em-webhook`.

### Quick test without CPI

```powershell
curl.exe -X POST https://em-webhook-mediating-fossa-mx.cfapps.us10.hana.ondemand.com -H "Content-Type: application/json" -d '{\"test\": 1}'
```

It shows up in `cf logs em-webhook`.

---

## 2. CPI side

### 2.1 Security material (Monitor → Integrations and APIs → Security Material)

**Create → OAuth2 Client Credentials** (for Event Mesh), values from `.env`:

| Field | Value |
|---|---|
| Name | `EventMesh_OAuth` |
| Token Service URL | `EM_TOKEN_URL` |
| Client ID | `EM_CLIENT_ID` |
| Client Secret | `EM_CLIENT_SECRET` |
| Client Authentication | Send as Request Header |
| Include Scope | off |

### 2.2 iFlow

```
[Sender: EventMesh] ──AMQP──► (Start) ──► (End) ──HTTP──► [Receiver: Webhook]
```

**Sender adapter: AMQP** (Event Mesh → iFlow)

| Tab | Field | Value |
|---|---|---|
| Connection | Transport Protocol | WebSocket |
| | Message Protocol | AMQP 1.0 |
| | Host | `bms-isd-9555164b-ab69-4396-8737-d15e71a46e92.us10.a.eventmesh.integration.cloud.sap` |
| | Port | `443` |
| | Path | `/protocols/amqp10ws` |
| | Proxy Type | Internet |
| | Authentication | OAuth2 Client Credentials |
| | Credential Name | `EventMesh_OAuth` |
| Processing | Queue Name | `queue:s4-queue-HACKATHON` |
| | Consumers | 1 |

**Receiver adapter: HTTP** (iFlow → webhook)

| Field | Value |
|---|---|
| Address | `https://em-webhook-mediating-fossa-mx.cfapps.us10.hana.ondemand.com` |
| Proxy Type | Internet |
| Method | POST |
| Authentication | None |
| Throw Exception on Failure | on |

**Save → Deploy.** Monitor → Manage Integration Content must show **Started**.

> Only one consumer should read `s4-queue-HACKATHON`. Undeploy any other iFlow using
> the same queue, otherwise it takes the messages instead.

---

## 3. Test end to end

1. Run `cf logs em-webhook` and leave it open.
2. iFlow deployed and **Started**.
3. Publish an event (S/4HANA, or a message to the queue/topic from the Event Mesh UI).
4. The log window prints:

```
[APP/PROC/WEB/0] OUT ===== Message received ... =====
[APP/PROC/WEB/0] OUT { "SalesOrder": "4711", ... }
```

---

## 4. Troubleshooting

| Symptom | Check |
|---|---|
| Nothing in the logs, iFlow shows no runs | Queue subscribed to the topic? Another consumer on the queue? |
| iFlow run **Failed** at the HTTP adapter | Address matches the webhook URL? `cf app em-webhook` shows `running`? |
| iFlow does not start (AMQP error) | Host/Port/Path, credential name, `queue:` prefix in Queue Name |
| App crashed | `cf logs em-webhook --recent` |
