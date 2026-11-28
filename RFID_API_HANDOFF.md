# RFID 与电子纸接口交接

本文描述当前本地演示的实际流程和接口，供服务器开发同学对接。它不是云端 API 已经实现的证明。

相关代码：[Mega 固件](hardware_updated/hardware_updated_RFID_lock/hardware_updated_RFID_lock.ino)、[ESP32 桥接固件](NO-PUSH-explaination/wifi_updated/wifi_updated_RFID/wifi_updated_RFID.ino)、[本地服务器](server.py)、[网页 RFID 面板](js/pages/technology-showcase.js)。

## 1. 拍卡后的流程

```text
RFID 卡 -> Mega 判断卡片角色 -> ESP32 -> Wi-Fi -> 服务器
                                                    |
网页选择展品、保存描述 <--------- pending_edit ------+
          |
          v
服务器保存描述，结束修改 -> ESP32 读取描述 -> Mega 更新电子纸
                                  |
                                  +-> 向服务器确认更新
```

1. Mega 读取卡片 UID，并依据本地卡片列表决定角色。
2. `door` 卡：Mega 在本地触发继电器约 3 秒，并向 ESP32 发送刷卡事件。
3. `epaper` 卡：不开门；两盏 RGB 灯短暂变绿，并向 ESP32 发送刷卡事件。
4. 未注册卡：不触发门锁，发送 `denied` 事件。
5. ESP32 将事件通过 HTTP POST 发送到服务器。服务器在空闲时收到 `door` 或 `epaper` 事件，进入 `pending_edit`。
6. 网页读取状态，工作人员选择展品、填写描述并保存。服务器保存描述后回到 `idle`。
7. ESP32 定期读取描述，发现内容变化后通过串口发送给 Mega。Mega 刷新电子纸并确认，ESP32 再向服务器报告确认。

**不需要第二次拍卡。** 网页通过保存描述结束修改；已配置的 `edit` 卡可以不发布描述而结束待处理的修改。

**重要区别：**门锁由 Mega 本地判断并控制，不等待服务器批准。服务器收到刷卡事件不代表它控制了门锁，也不能证明门实际上已经打开。断网时，本地认可的门卡仍可开门。绿灯只表示 Mega 本地识别了电子纸卡，不代表服务器或电子纸更新成功。

拍 `epaper` 卡时，如果服务器已有描述，ESP32 会尝试读取已保存的描述；如果尚无描述，才会读取并显示服务器流程状态。保存新描述之后，ESP32 会自动再检查和更新，不需要拍第二次卡。

## 2. 当前本地 HTTP 接口

本地 RFID 流程严格使用以下 **5 个请求方法和路径**。状态、最新刷卡事件和电子纸送达状态统一由 `GET /api/rfid/status` 返回；不另设查询接口。以下路径是本地 `server.py` 的接口，不代表云端已实现。示例 UID、展品 ID 和描述均为示例，不是真实卡片数据。

| # | 请求 | 用途 |
|---|---|---|
| 1 | `POST /api/rfid/device-event` | ESP32 上传刷卡事件 |
| 2 | `GET /api/rfid/status` | 网页和 ESP32 查询流程、最近事件和电子纸送达状态 |
| 3 | `POST /api/rfid/publish-description` | 网页保存描述并结束修改 |
| 4 | `GET /api/rfid/epaper-description` | ESP32 获取待显示文字 |
| 5 | `POST /api/rfid/epaper-delivery` | ESP32 报告 Mega 已确认更新 |

### 2.1 ESP32 上传刷卡事件

`POST /api/rfid/device-event`  
`Content-Type: application/json`

```json
{
  "type": "rfid_event",
  "uid": "A1B2C3D4",
  "result": "accepted",
  "exhibit_id": "helmet-001",
  "role": "door"
}
```

- `type`：固定为 `rfid_event`。
- `uid`：4–20 个十六进制字符；服务器返回大写。
- `result`：`accepted` 或 `denied`，这是 Mega 的本地判断。
- `role`：当前事件角色包括 `door`、`epaper` 和 `edit`。
- `exhibit_id`：认可的事件必须提供；目前 Mega 固定发送 `helmet-001`，**不是识别出的网页展品 ID**。
- 未注册卡的实际请求不带 `role` 和 `exhibit_id`：

```json
{
  "type": "rfid_event",
  "uid": "11223344",
  "result": "denied"
}
```

成功返回 `200`，响应体为规范化后的事件 JSON；请求不合法返回 `400`。ESP32 对事件 POST 的任意 2xx 视为成功。

服务器处于 `idle` 时，认可的 `door` 或 `epaper` 事件开始 `pending_edit`；已处于 `pending_edit` 时，后续开始事件不会替换当前会话。`edit` 事件会结束待处理会话，不发布描述。服务器也兼容不带 `role` 的旧版认可事件。

### 2.2 网页读取流程状态

`GET /api/rfid/status`

```json
{
  "data": {
    "phase": "pending_edit",
    "uid": "A1B2C3D4",
    "exhibit_id": "helmet-001",
    "edit_id": "example-session-id",
    "latest_event": {
      "type": "rfid_event",
      "uid": "A1B2C3D4",
      "result": "accepted",
      "exhibit_id": "helmet-001",
      "role": "door"
    },
    "epaper_delivery": "pending"
  }
}
```

`edit_id` 由服务器生成，用于防止过期的网页提交到新会话。空闲时 `phase` 是 `idle`，`uid`、`exhibit_id` 和 `edit_id` 为 `null`。`latest_event` 是最近一次刷卡事件（没有事件时为 `null`），不是完整事件历史。`epaper_delivery` 为 `not_saved`、`pending` 或 `confirmed`。网页 RFID 面板约每 3 秒查询一次；ESP32 在没有已保存描述、需要显示流程状态时，也查询此接口。

### 2.3 网页保存描述并结束修改

`POST /api/rfid/publish-description`  
`Content-Type: application/json`

```json
{
  "edit_id": "example-session-id",
  "exhibit_id": "ex-014",
  "description": "A red balloon."
}
```

- `edit_id` 必须匹配当前 `pending_edit` 会话。
- `exhibit_id` 由工作人员在网页上选择，可能不同于硬件固定发送的 ID。
- `description` 必须是 1–180 个可打印 ASCII 字符，且不能全为空格；当前电子纸不支持中文。
- 成功返回 `200` 和 `{"data":{"phase":"idle","uid":null,"exhibit_id":null,"edit_id":null}}`。会话不匹配返回 `409`；输入错误返回 `400`；保存失败返回 `500`。
- 网页展品资料目前只保存到浏览器本地，服务器另外保存最近一次发布的电子纸描述；两者不是云端共享数据库。

如果不发布描述，已配置的 `edit` 卡可以通过现有 `POST /api/rfid/device-event` 结束待处理会话；不需要额外的“结束修改”接口。

### 2.4 ESP32 获取电子纸描述

`GET /api/rfid/epaper-description`

- `200`：响应体是 `text/plain`，只包含描述文字，不是 JSON，例如：

```text
A red balloon.
```

- `404`：尚未保存描述。
- `500`：读取已保存描述失败。

ESP32 约每 10 秒轮询；串口传输、网络失败或队列等可能导致延迟。只有文字变化时才重新发送给 Mega；Mega 重启后也会重发。若 `epaper` 卡触发时没有保存过描述，ESP32 调用 `GET /api/rfid/status`，读取其中的 `phase`，再交给 Mega 显示。

### 2.5 ESP32 确认更新

`POST /api/rfid/epaper-delivery`  
`Content-Type: text/plain`

请求体是已经由 Mega 确认的完整描述，例如：

```text
A red balloon.
```

服务器核对其与当前已保存描述一致后返回：

```json
{"data":{"state":"confirmed"}}
```

状态为 `200`。当前描述不存在或已改变时返回 `409`；输入错误返回 `400`。ESP32 要求确认接口返回 `200`。

网页从 `GET /api/rfid/status` 的 `epaper_delivery` 字段查看状态。`confirmed` 表示 Mega 曾确认更新，不是实时在线状态，也不是人工目视确认。

## 3. 云端对接需要讨论

提供的 [OpenAPI 0.4.1](NO-PUSH-explaination/openapi%20(4).json) 有 `/api/tag-scans`、`/api/commands`、命令确认接口，以及 `/staff-api/label-drafts/{exhibit_id}` 等。但这些与上面的本地路径、请求格式和认证不同，不能只替换服务器地址就假定可用。请服务器、硬件和网页同学共同确定：

1. 云端兼容本地接口，还是修改 ESP32 和网页适配现有云端接口。
2. 展柜设备 ID、展品 ID 的对应关系，以及服务器如何持久化流程和描述。
3. 设备身份认证、HTTPS、网页员工权限、超时、失败重试和事件去重。
4. 内容版本和设备确认机制，以及离线、重启和丢失数据的处理。

当前 ESP32 代码使用普通 HTTP 客户端，且从 RFID URL 推导电子纸接口路径；接入云端 HTTPS、认证或不同路径需要修改固件。现有本地 API 无生产认证，只应在可信网络测试。ESP32 的待发送事件队列最多 4 条，满时新事件不会入队；HTTP 成功确认丢失后可能重试，因此不要假定事件恰好发送一次。

本地流程状态和电子纸确认保存在服务器内存，服务器重启会重置；最近保存的描述写入本地文件。`GET /api/rfid/status` 中的 `latest_event` 只保存最近事件，不应视作持久审计日志。不要提交 Wi-Fi 密码、API 密钥或真实卡片 UID。

## 4. 给云端同学的具体交接

### 希望云端支持的功能

以下是**待共同确认和实现的需求**，不是声称云端已经支持，也不要求一定照搬本地路径：

| 功能 | 发送方 | 云端需要提供的能力 |
|---|---|---|
| 上传拍卡事件 | ESP32 | 接收 `door`、`epaper` 和拒绝事件；记录设备、事件与处理结果。避免重试重复创建修改会话。 |
| 查询状态 | 网页和 ESP32 | 一个状态接口同时返回修改会话、最近刷卡事件和电子纸送达状态；并发拍卡时不覆盖正在进行的会话。 |
| 发布描述 | 网页 | 验证员工权限和会话 ID，保存所选展品和描述，并结束会话；同时保留待设备确认的更新。 |
| 获取待显示内容 | ESP32 | 让设备读取对应展柜的当前描述或待处理更新；首次尚无内容时返回明确状态。 |
| 确认设备更新 | ESP32 | 收到 Mega 的更新确认后记录对应设备和内容版本；网页可区分待确认和已确认。 |

`door` 的开门动作仍由 Mega 本地执行；云端接口不应被误认为开门命令或实际门状态。网页保存结束会话，不代表电子纸已经收到文字。

### 请云端同学回复的信息

1. 选择**兼容第 2 节**，还是**使用现有云端接口并修改固件和网页**；请给出正式 OpenAPI 或接口文档。
2. 每个接口的 URL、方法、请求和响应示例、状态码，以及 ESP32 和员工网页各自的认证方式。
3. 设备 ID、展品 ID、会话 ID、事件去重 ID 和描述版本怎样生成与关联；多展柜如何隔离。
4. 已有哪些功能部署可用，哪些仍需开发；可供联调的测试环境及测试账号或设备凭证的安全获取方式。**不要把密钥或真实 UID 写进文档或 Git。**

若使用现有云端接口，请逐项说明它与上表五项功能的对应关系，包括 `pending_edit` 会话与电子纸确认如何实现。现有本地固件只通过**描述文本本身**比对确认，没有事件去重 ID、设备 ID 或内容版本；加入这些能力需要同步修改固件和网页，不能仅靠云端实现。

### 联调验收清单

- [ ] `door` 卡本地开门、云端收到事件并建立待修改会话；服务器不可用时不误报云端已收到。
- [ ] `epaper` 卡不开门、云端收到事件并建立待修改会话；未知卡被拒绝且不创建会话。
- [ ] 在已有会话期间再次拍卡，不覆盖当前会话；网络重试不重复创建会话。
- [ ] 网页选展品并提交描述后会话结束；过期会话提交被拒绝；不发布结束不会修改原描述。
- [ ] 设备拿到新描述后更新电子纸；更新前网页显示待确认，Mega 确认后才显示已确认。
- [ ] 更新失败、设备断网、服务器重启及重连后，状态和重试符合双方约定；旧内容的确认不能错误确认新版本。
- [ ] 验证不同展柜不会互相覆盖数据；员工及设备无权访问或操作其他未授权资源。

本地现有实测和服务器自动测试不能代替上述**云端与实物联调**；请记录实际测试结果。
