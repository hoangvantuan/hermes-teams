# Kiểm chứng giả định Teams / Graph cho plugin dựng lại thread kênh

Ngày kiểm tra: 2026-11-20. Chỉ dùng nguồn gốc của Microsoft. Không gọi tenant thật.

**BLUF (kết luận trước):**

- **V1:** Dùng `channelData.replyChain.id` làm khoá thread là đúng hướng. Tính năng này vẫn **preview** (tài liệu ngày 2026-11-17). Hậu tố `;messageid=` trên cả L1 và L2: **chưa rõ**, và Microsoft dặn không phân tích `conversation.id`. `activity.id` trùng `id` trong Graph: **chưa có câu nào khẳng định trực tiếp**. Có chuỗi bằng chứng gián tiếp mạnh, nhưng vẫn cần thử trên tenant thật.
- **V2:** Quy trình "protected API" (xin duyệt bằng form) **đã bỏ từ 2023-05-18**. Endpoint `/replies` và `GET message` **không còn** bị chặn bởi quy trình đó. Quyền ít nhất cho token app-only là `ChannelMessage.Read.Group` (RSC). Teams API **không còn tính phí** từ 2025-08-25.
- **V5:** Tin do bot gửi có `from.user = null` và `from.application.applicationIdentityType = "bot"`. Chưa có tài liệu nói `from.application.id` bằng App ID của bot. Nên dùng kết hợp hai cách: lưu id của tin bot gửi đi, và so `from.application.id` với App ID. Cả hai cách đều chờ thử thật (cách 1 phụ thuộc V1b).
- **V7:** `GET …/replies` và `GET …/messages/{id}` không có dòng riêng, nên dùng mức mặc định: **1 rps cho mỗi kênh**, 30 rps cho mỗi app trong mỗi tenant. Khi gặp 429, chờ đúng `Retry-After`.
- **V8:** Graph có `lastEditedDateTime` và `deletedDateTime`. Tin đã xoá "không phải lúc nào cũng được trả về". Bot có nhận `messageUpdate`/`messageDelete` trong kênh khi chỉ có RSC hay không: **chưa rõ**, mọi ví dụ trong tài liệu là chat 1-1.

## Cách kiểm chứng

- Em đọc **mã nguồn markdown trên GitHub**, không đọc trang learn.microsoft.com đã dựng. Số dòng tính theo file `.md` tại commit được ghim dưới đây.
- Commit đã đọc:
  - `MicrosoftDocs/msteams-docs` @ `49fdb2a90a5f8ab1195b51435fbaf2f8ee84874a` (2026-11-17). Viết tắt **[MT]** = `https://github.com/MicrosoftDocs/msteams-docs/blob/49fdb2a90a5f8ab1195b51435fbaf2f8ee84874a/msteams-platform/`
  - `microsoftgraph/microsoft-graph-docs-contrib` @ `2329b086341e67d36de55a5743af1a47224046ca` (2026-11-13). Viết tắt **[GD]** = `https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/2329b086341e67d36de55a5743af1a47224046ca/`
  - `microsoft/teams.py` nhánh `main` @ `59359964408dc749923c9529365320efa86e96a2` (2026-11-09). Viết tắt **[TP]** = `https://github.com/microsoft/teams.py/blob/59359964408dc749923c9529365320efa86e96a2/`. Một số điểm đối chiếu thêm với tag `v2.1.1` (bản Hermes ghim) qua `gh api …?ref=v2.1.1`.
- Hai file đã bị xoá khỏi kho Graph được đọc ở commit lịch sử (ghi rõ ở V2).

---

## V1. `conversation.id`, `activity.id`, `replyToId`, `channelData.replyChain`

### V1a. `conversation.id` có `;messageid=<rootId>` cho cả L1 và L2?

**Kết luận: Chưa rõ.**

- Tài liệu chỉ có **ví dụ** mang hậu tố này. Không có câu nào mô tả quy tắc cho L1 và L2:
  - `"id": "19:aebd0ad4d6ab42c8b9ed19c251c2fc37@thread.skype;messageid=1481567603816"` — [MT]`bots/how-to/conversations/channel-and-group-conversations.md` dòng 195 (ví dụ activity có mention trong kênh).
  - `"id": "19:6decf54d86d945e4b3924b63a9161a78@thread.skype;messageid=1611060744833"` — [MT]`messaging-extensions/how-to/action-commands/create-task-module.md` dòng 320 (invoke từ kênh).
- Microsoft dặn rõ không dựa vào nó: "Don't assume a particular meaning for `conversation.id` or use it as the storage key for this data. Treat it as an opaque value: don't parse it or use it to distinguish L1 from L2." — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 55.
- Thông báo lỗi của API gửi tin gọi phần này là "reply-chain id (;messageid=)" — cùng file, dòng 290. SDK cũng ghi: "Strip any existing ;messageid= suffix (mirrors the service's conversation-ID normalization)" — [TP]`packages/apps/src/microsoft_teams/apps/utils/thread.py` dòng 27.

**Ảnh hưởng tới thiết kế:** Giữ nguyên thứ tự dự phòng ở mục 3.1 của tài liệu gốc. Bước 3 (đọc `;messageid=`) chỉ là dự phòng cuối, có ghi log khi dùng tới. Không dùng nó để phân biệt L1/L2.

**Cần thử thật:** Có. Ghi lại payload thô của một tin L1 và một tin L2 trong cùng thread.

### V1b. `activity.id` (và `replyToId`) có trùng `id` của chatMessage trong Graph?

**Kết luận: Chưa rõ cho `activity.id` (khả năng cao là đúng, nhưng chỉ là suy luận). Chưa rõ cho `replyToId` (bằng chứng trái chiều).**

Chuỗi bằng chứng gián tiếp (suy luận, không phải câu khẳng định):

1. Trong cùng một activity, hậu tố `;messageid=1611060744833` (dòng 320) trùng `messagePayload.id` `"1611060744833"` (dòng 344) và trùng `messageId` trong deep link `…/l/message/19:…@thread.skype/1611060744833` (dòng 343) — [MT]`messaging-extensions/how-to/action-commands/create-task-module.md`.
2. Graph dùng cùng dạng deep link với `chatMessage.id`: `"id": "1614618259349"` (dòng 236) và `"webUrl": "https://teams.microsoft.com/l/message/19%3A…%40thread.tacv2/1614618259349?…"` (dòng 249) — [GD]`api-reference/v1.0/api/chatmessage-get.md`.
3. Bot Framework so `activity.id` với root id để phân loại L2: "User-typed `message`, `messageUpdate`, or `messageDelete` | `activity.id` differs from the root message ID." — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 304.
4. SDK mô tả `replyChain.id`: "the id of the message at the root of the reply chain (thread), for example `1793384051102`. For a top-level message it equals the message's own id." — [TP]`packages/api/src/microsoft_teams/api/models/channel_data/reply_chain_info.py` (docstring trường `id`); cùng ý ở [TP]`packages/api/src/microsoft_teams/api/models/channel_data/channel_data.py` dòng 51–60.

Phản ví dụ cần lưu ý:

- Ví dụ activity kênh (mẫu từ 2017) có `"replyToId": "3UP4UTkzUk1zzeyW"` — [MT]`bots/how-to/conversations/channel-and-group-conversations.md` dòng 218. Đây không phải dạng số như id của Graph.
- Ngược lại, bảng phân loại L2 so `replyToId` với root message ID cho reaction, phản hồi card và invoke ("`replyToId` differs from the root message ID") — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 305–308. Điều này gợi ý `replyToId` cùng không gian id với root, ít nhất cho các loại activity đó.
- Hai nguồn trái chiều, nên không giả định `activity.replyToId` bằng `chatMessage.replyToId` của Graph.
- Graph: "IDs are unique within a chat/channel/reply-to-message, but might be duplicated in other chats/channels/reply-to-messages." — [GD]`api-reference/v1.0/resources/chatmessage.md` dòng 76. Khoá lọc trùng phải gồm cả id kênh và id root.

**Ảnh hưởng tới thiết kế:** Lọc trùng theo `(channel_id, root_id, message_id)`, không theo `message_id` một mình. Không dùng `activity.replyToId` để tìm root; dùng `replyChain.id`. Thêm bước dự phòng khi không khớp id: so theo `createdDateTime` cộng người gửi, và ghi log để phát hiện sớm.

**Cần thử thật:** Có. So `activity.id` của một tin L2 với `id` trả về từ `GET …/messages/{root}/replies`.

### V1c. Trạng thái `channelData.replyChain`

**Kết luận: Đúng là preview. Chưa có ngày GA (phát hành chính thức).**

- "The threading capabilities described in this article are in preview. They are available in channels and one-on-one agent chats…" — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 18; `ms.date: 11/17/2026` ở dòng 5.
- "Both top-level messages (L1) and thread replies (L2) include `channelData.replyChain.id` when threading is supported." và "If the field is absent, don't attempt a thread reply for that activity." — cùng file, dòng 42 và 44.
- Khoá lưu theo thread: "use the containing chat or channel ID together with the L1 root message ID (`channelData.replyChain.id`)" — dòng 53.
- SDK: "This API is in preview and may change in the future. Diagnostic: ExperimentalTeamsThreading" — [TP]`…/channel_data/channel_data.py` (docstring `reply_chain`, dòng 59–60).
- Tag `v2.1.1` của SDK không có `reply_chain_info.py` trong `packages/api/src/microsoft_teams/api/models/channel_data/` (kiểm tra bằng `gh api …/contents/…?ref=v2.1.1`: chỉ có `app_info, channel_data, channel_info, feedback_loop, notification_info, settings, team_info, tenant_info`).

**Ảnh hưởng tới thiết kế:** Trường có thể vắng mặt hoặc đổi tên. Plugin phải chạy được khi thiếu nó (rơi về `;messageid=` hoặc coi là L1). Trên SDK 2.1.1 phải đọc qua `model_extra`.

**Cần thử thật:** Có. Xác nhận tenant đang nhận `replyChain` trong kênh.

---

## V2. "Protected API", quyền RSC, giấy phép và tính phí

### V2a. `/replies` và `/messages/{id}` còn là "protected API" phải xin duyệt?

**Kết luận: Sai — quy trình xin duyệt đã bỏ từ 2023-05-18.** Trước đây danh sách này **có** gồm `/replies` và get message, không chỉ API xuất dữ liệu.

- Bản cuối của trang trước khi bị xoá: "Starting May 18, 2023, the protected API approval process has been deprecated. You can now call APIs that were previously protected as long as the requirements for accessing without a user are met." — `concepts/teams-protected-apis.md` dòng 12 tại commit `ce79b319c5568a8099c1df7447fc2ca9f70e8cf7`: https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/ce79b319c5568a8099c1df7447fc2ca9f70e8cf7/concepts/teams-protected-apis.md#L12
- Danh sách cũ ở dòng 18–23 của file đó có "List channel messages", "Get channel message", "List replies to a message", "Get a reply to a message".
- File bị xoá ở commit `05a7c71e17d3a16213e49cd154fbc7f9b9c226a2` (2023-06-26), thông điệp "Delete teams-protected-apis.md".
- Trang hiện tại vẫn còn một câu chung: "Some metered APIs and services in Microsoft Graph are protected and require additional validation… you must submit a request." — [GD]`concepts/metered-api-list.md` dòng 18. Bảng ngay dưới chỉ còn `assignSensitivityLabel` của SharePoint/OneDrive, không có API Teams nào. Em hiểu câu này không áp dụng cho Teams (suy luận).
- Trang API hiện tại không nhắc "protected", không nhắc giấy phép: grep `protected|licens|model=|metered` trong [GD]`api-reference/v1.0/api/chatmessage-list-replies.md`, `chatmessage-get.md`, `channel-list-messages.md` không ra kết quả.

### V2b. RSC `ChannelMessage.Read.Group` cho phép gì?

**Kết luận: Đúng — đây là quyền application (app-only) ít nhất cho cả hai endpoint.**

- `/replies`: bảng quyền "Application | ChannelMessage.Read.Group | ChannelMessage.Read.All, Group.Read.All, Group.ReadWrite.All" — [GD]`api-reference/v1.0/includes/permissions/chatmessage-list-replies-permissions.md` dòng 11; ghi chú "The ChannelMessage.Read.Group permission uses resource-specific consent" — [GD]`api-reference/v1.0/api/chatmessage-list-replies.md` dòng 29–30.
- `GET /messages/{id}` và `GET …/replies/{reply-id}`: "Application| ChannelMessage.Read.Group, ChannelMessage.Read.All, Group.Read.All, Group.ReadWrite.All" — [GD]`api-reference/v1.0/api/chatmessage-get.md` dòng 29, đường dẫn ở dòng 49–50.
- Nội dung quyền: "`ChannelMessage.Read.Group` | Read and receive all of this team's channel messages. See […] | App-only" — [MT]`graph-api/rsc/resource-specific-consent.md` dòng 66.
- Phạm vi: quyền kênh tự mở rộng tới mọi kênh của team "except private and shared channels"; với kênh riêng tư/chia sẻ chỉ khi app được thêm vào kênh đó — cùng file, dòng 56.
- Ai cấp: "only administrators or resource owners can grant app-only RSC permissions" — dòng 31. Bảng ở dòng 39 ghi tenant có "Microsoft Entra admin center-based controls for Teams".
- Không có RSC: "the only chat messages that an agent receives or can access through Graph APIs are the messages in which it's @mentioned" — [MT]`agents-in-teams/enable-receive-all-chat-messages.md` dòng 11.
- Kiểm tra quyền đã cấp: `GET /teams/{team-id}/permissionGrants`, so `clientAppId` với `webApplicationInfo.id` — [MT]`graph-api/rsc/declare-resource-specific-consent-requirements.md` dòng 173–185; lệnh này cần `TeamsAppInstallation.ReadForTeam` (dòng 150).

### V2c. Giấy phép và tính phí (2026)

**Kết luận: Đúng — không tính phí, không cần cấu hình billing.**

- "Starting August 25, 2025, the Teams APIs are no longer metered, and no billing configuration is required to use these APIs." — [GD]`concepts/metered-api-list.md` dòng 14 (`ms.date: 08/06/2025`).
- Trang giấy phép `concepts/teams-licenses.md` **không còn ở HEAD** (lỗi 404 qua `gh api`; commit cuối chạm đường dẫn là `5a88c6f33d`, 2026-10-08). Bản tại commit `aacc609973a7fe58e562713812f56fa9f3c4462a` (2026-03-16) ghi: "the model query parameter is no longer required and will be ignored if supplied. License enforcement is not applicable unless explicitly specified in the documentation for the respective API." (dòng 14–16). Ngoại lệ (dòng 19–21) chỉ gồm meeting AI insights và DLP Patch. Dòng 23: "This article is deprecated and will be removed in June 2026."

**Ảnh hưởng tới thiết kế:** Không cần form xin duyệt, không cần Azure subscription cho billing. Cần: (1) app Entra trùng `webApplicationInfo.id`; (2) chủ team hoặc admin đồng ý RSC khi cài; (3) chính sách RSC của tenant không chặn. Xử lý 403 bằng thông báo rõ "thiếu RSC grant", không lặp lại.

**Cần thử thật:** Có, nhưng chỉ để xác nhận cấu hình tenant: chạy `GET /teams/{id}/permissionGrants`, rồi một lệnh `GET …/replies` bằng token app-only.

---

## V5. `from` của tin do bot gửi trong Graph

**Kết luận: Hình dạng `from` — Đúng. "`from.application.id` = App ID của bot" — Chưa rõ.**

- Ví dụ chính thức cho bot Bot Framework: `"from": { "device": null, "user": null, "application": { "id": "358f0194-…", "displayName": "Flow", "applicationIdentityType": "bot" } }` — [GD]`concepts/teams-messaging-overview.md` dòng 453–466.
- Định nghĩa: `application` "If present, represents the application (for instance, bot) that sent the message." — [GD]`api-reference/v1.0/resources/chatmessagefromidentityset.md` dòng 23.
- `applicationIdentityType` có các giá trị `aadApplication`, `bot`, `tenantBot`, `office365Connector`, `outgoingWebhook`, `unknownFutureValue`; `id` là "ID of the application" — [GD]`api-reference/v1.0/resources/teamworkapplicationidentity.md` dòng 23, 25.
- Webhook gửi đi có `applicationIdentityType: "outgoingWebhook"` — [GD]`concepts/teams-messaging-overview.md` dòng 468–479.
- Bot gửi thay mặt người dùng có thêm `onBehalfOf` — cùng file, dòng 663.
- Không tìm thấy câu nào nói `application.id` bằng Microsoft App ID của bot. Phạm vi tìm: grep `bot's app id|botId|Microsoft App ID|bot ID` trong ba file Graph (`teams-messaging-overview.md`, `teamworkapplicationidentity.md`, `chatmessagementionedidentityset.md`), và grep `"application": {` / `applicationIdentityType` trong các thư mục `bots`, `graph-api`, `agents-in-teams`, `messaging-extensions`, `resources`, `concepts` của `msteams-platform`. Không quét toàn bộ hai kho.
- Bot khác (kể cả Workflows) cũng mang `applicationIdentityType: "bot"`, nên chỉ xét kiểu là không đủ. Teams cũng không chuyển tin của bot khác tới bot, nhưng Graph vẫn đọc được — [MT]`agents-in-teams/enable-receive-all-chat-messages.md` dòng 13.

**Ảnh hưởng tới thiết kế:** Dùng kết hợp hai cách bổ sung cho nhau. Cả hai đều chờ thử thật:

1. Lưu `id` trả về khi bot gửi tin (ResourceResponse `{"id": "{messageId}"}` — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 255–263), theo khoá `(channel_id, root_id, id)`. Cách này chỉ đúng nếu id đó trùng `id` trong Graph, điều V1b còn **chưa rõ**. Có thêm lỗ hổng: khi Teams tách một activity gồm văn bản và tệp đính kèm thành hai, "you do not receive the message ID in response" — [MT]`bots/build-conversational-capability.md` dòng 557.
2. `from.application.id == <bot app id>` và `applicationIdentityType == "bot"`. Cách này phụ thuộc điểm "Chưa rõ" ở trên.
3. Tin có `application` khác app id của bot thì coi là bot khác, giữ làm ngữ cảnh nhưng gắn nhãn.

**Cần thử thật:** Có. Đọc qua Graph một tin bot vừa gửi, so `from.application.id` với App ID.

---

## V7. Giới hạn tốc độ khi đọc tin kênh

**Kết luận: Đúng như tài liệu gốc mô tả. Giới hạn trói buộc là 1 rps cho mỗi kênh.**

- Throttling tính theo bốn chiều độc lập: per app, per app per tenant, per resource, per user; vượt **bất kỳ** chiều nào là bị chặn — [GD]`includes/throttling-teams.md` dòng 9–16.
- `GET /teams/{team-id}/channels/{channel-id}/messages`: 200 rps per app, 20 rps per app per tenant, 1 rps per channel — dòng 52.
- `GET …/replies` và `GET …/messages/{id}` không có dòng riêng. Dấu "-" hoặc không có dòng thì áp mức mặc định (dòng 22, 145): GET 1500 rps per app, 30 rps per app per tenant, 1 rps per chat or channel, 1 rps per user — dòng 149.
- "A sustained limit of approximately 83 percent of the listed value is also evaluated over a longer window… use exponential backoff." — dòng 20.
- Khi bị 429: "Wait the number of seconds specified in the `Retry-After` header. Retry the request. If the request fails again with a 429 error code… Continue to use the recommended `Retry-After` delay" — [GD]`concepts/throttling.md` dòng 82–86. Không có `Retry-After` thì dùng exponential backoff — dòng 92–93.
- Quy định polling: "If your app polls to see whether a resource has changed, you can only do that once per day… you should create a subscription" — [GD]`api-reference/v1.0/resources/teams-api-overview.md` dòng 83–85. Vi phạm có thể bị throttle thêm hoặc bị đình chỉ (dòng 95–97).
- `$top` của `/replies` tối đa 50 — [GD]`api-reference/v1.0/api/chatmessage-list-replies.md` dòng 41.

**Ảnh hưởng tới thiết kế:**

- Chỉ đọc Graph khi có sự kiện (tin mới tới bot), không chạy vòng lặp kiểm tra.
- Dựng lại thread tốn 1 lệnh GET root + `ceil(n/50)` lệnh `/replies`. Đặt hàng đợi theo kênh, tối đa ~0,8 lệnh/giây mỗi kênh.
- Ưu tiên cache: lần đầu dựng từ Graph, các lần sau nối thêm tin đến từ activity (đã có RSC).
- Nếu cần cập nhật liên tục, dùng change notifications thay cho polling.

**Cần thử thật:** Không bắt buộc. Nên có kiểm thử đơn vị cho nhánh 429 có/không có `Retry-After`.

---

## V8. Tin bị sửa / xoá

### V8a. Graph thể hiện thế nào?

**Kết luận: Đúng về trường dữ liệu. Chưa rõ về việc `/replies` có trả tin đã xoá.**

- `lastEditedDateTime`: "Timestamp when edits to the chat message were made. Triggers an "Edited" flag in the Teams UI. If no edits are made the value is `null`." — [GD]`api-reference/v1.0/resources/chatmessage.md` dòng 78.
- `lastModifiedDateTime` thay đổi cả khi thêm/bỏ reaction — dòng 79. Vì vậy không dùng nó để phát hiện sửa nội dung.
- `deletedDateTime`: "Timestamp at which the chat message was deleted, or null if not deleted." — dòng 72.
- "**Note:** Deleted messages are not always returned by all APIs. Change notifications always return deleted message notifications if the changeType of the subscription includes `deleted`." — [GD]`concepts/teams-messaging-overview.md` dòng 365.
- Các API bám theo giao diện Teams (gồm `/replies`) có "any changes (messages sent, edited, or deleted) are immediately visible" — cùng file, dòng 792, 798.
- Không có ví dụ nào trong tài liệu cho một tin đã xoá (grep `"deletedDateTime": "20…` không có kết quả).
- Change notifications cho kênh hỗ trợ `changeType: "created,updated,deleted"` và quyền application `ChannelMessage.Read.Group*` (RSC) — [GD]`concepts/teams-changenotifications-chatmessage.md` dòng 74, 97–99.

### V8b. Bot có nhận `messageUpdate` / `messageDelete` khi chỉ có RSC?

**Kết luận: Chưa rõ cho kênh.**

- Tài liệu chung: "When you edit a message, the agent gets a notification of the edit message activity." — [MT]`bots/build-conversational-capability.md` dòng 343. Xoá mềm tương tự ở dòng 664. Xoá mềm có `channelData.eventType` là `softDeleteMessage` — [MT]`bots/bot-concepts.md` dòng 305. Ba giá trị `softDeleteMessage`, `undeleteMessage`, `editMessage` có trong route của SDK (xem dòng dưới).
- Cả ba payload ví dụ đều là `"conversationType": "personal"` (dòng 400, 625, 719). Không có ghi chú nào về kênh, mention hay RSC trong các mục này.
- Gián tiếp: bảng phân loại L2 có nhắc `messageUpdate`/`messageDelete` — [MT]`bots/how-to/conversations/agent-chat-threads.md` dòng 304. Điều này gợi ý chúng có xuất hiện trong thread, nhưng không nói về điều kiện RSC.
- SDK `v2.1.1` có sẵn route cho `softDeleteMessage`, `undeleteMessage`, `editMessage` — https://github.com/microsoft/teams.py/blob/v2.1.1/packages/apps/src/microsoft_teams/apps/routing/activity_route_configs.py dòng 132, 154, 162 (nhánh `main` có cùng số dòng).

**Ảnh hưởng tới thiết kế:**

- Không phụ thuộc vào activity sửa/xoá. Mỗi lần dựng lại ngữ cảnh từ Graph, đọc `lastEditedDateTime`/`deletedDateTime` và bỏ tin đã xoá.
- Nếu có handler `messageUpdate`/`messageDelete` thì chỉ dùng để làm mất hiệu lực cache của thread.
- Cần cập nhật chắc chắn theo thời gian thực thì dùng change notifications với `deleted`.

**Cần thử thật:** Có. Sửa và xoá một tin L2 không mention bot, xem bot có nhận activity không, và `/replies` trả gì cho tin đã xoá.

---

## Bảng tổng hợp

| Mục | Kết luận | Cần thử thật |
|---|---|---|
| V1a `;messageid=` trên L1 và L2 | Chưa rõ (chỉ có ví dụ; Microsoft dặn không phân tích) | Có |
| V1b `activity.id` = Graph `id` | Chưa rõ (suy luận khả năng cao đúng); `replyToId`: Chưa rõ, bằng chứng trái chiều | Có |
| V1c `replyChain` | Preview (tài liệu 2026-11-17), chưa có ngày GA | Có |
| V2a Protected API cho `/replies`, get message | Sai — đã bỏ từ 2023-05-18 | Không (về quy trình) |
| V2b RSC `ChannelMessage.Read.Group` app-only | Đúng | Có (xác nhận grant) |
| V2c Tính phí / giấy phép | Không tính phí từ 2025-08-25; không có yêu cầu giấy phép trên trang API | Không |
| V5 Hình dạng `from` của bot | Đúng; `application.id` = App ID bot: Chưa rõ | Có |
| V7 Throttling | Đúng: 1 rps/kênh, 30 rps/app/tenant (mặc định GET); dùng `Retry-After` | Không bắt buộc |
| V8a Trường sửa/xoá trong Graph | Đúng; việc trả tin đã xoá: Chưa rõ | Có |
| V8b Activity sửa/xoá trong kênh với RSC | Chưa rõ | Có |

## Không lấy được / giới hạn

- Không đọc trang learn.microsoft.com đã dựng. Nội dung đã dựng có thể khác nhẹ với nhánh mặc định trên GitHub (ví dụ nhánh `live`).
- `teams-protected-apis.md` và `teams-licenses.md` đã bị xoá; em chỉ trích ở commit lịch sử.
- Kho `msteams-docs` chỉ được tải một phần (sparse checkout): `bots`, `graph-api`, `agents-in-teams`, `messaging-extensions`, `resources`, `concepts`, `sbs`. Kho Graph chỉ gồm `concepts`, `includes`, `api-reference/v1.0/resources`, `api-reference/v1.0/includes` và một số file `api-reference/v1.0/api/chatmessage*`, `channel-list-messages.md`. Câu trả lời cho các điểm "Chưa rõ" có thể nằm ở thư mục chưa quét.
- Không có tài liệu chính thức nào mô tả payload thật của L1/L2 trong kênh có `replyChain`; các kết luận "Chưa rõ" ở trên cần thử trên tenant thật.
