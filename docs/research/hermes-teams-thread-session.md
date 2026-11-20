# Hermes Agent × Microsoft Teams: một thread kênh = một session

> **Cập nhật 2026-11-20:** BLUF và mục 5 viết trước khi thử thật. Trên máy `solution`, `group_sessions_per_user: false` và `conversation.id` của reply đã có `;messageid=<root>`, nên việc tách session theo người và việc đặt `thread_id` không còn cần (xem `verify-live.md`). Nguồn quyết định duy nhất: `../PRD.md`.


> Ngày nghiên cứu: 2026-11-20. Mã nguồn Hermes đọc tại commit `9f47677483d1b4de4d1a88d37b3b7ca18b18b215` (nhánh `main`, 2026-11-19; release mới nhất là `v0.23.3`). Tài liệu Microsoft đọc từ kho nguồn chính thức trên GitHub (`MicrosoftDocs/msteams-docs`, `microsoftgraph/microsoft-graph-docs-contrib`). Các kho này là bản gốc của trang Microsoft Learn tương ứng.
>
> Nhãn dùng trong tài liệu: **[Sự thật]** = đã đọc thấy trong mã nguồn hoặc tài liệu, kèm trích dẫn. **[Giả định]** = suy luận chưa kiểm chứng. **[Khuyến nghị]** = đề xuất của em. **[Chưa kiểm chứng]** = cần thử trên hệ thống thật.

---

## 1. BLUF: kết luận trước

1. **Hermes đã có adapter Teams tích hợp sẵn**, nhưng adapter này không gán `thread_id` cho tin nhắn trong kênh. **[Sự thật]** `plugins/platforms/teams/adapter.py` dòng 526–533 gọi `build_source(chat_id=conv.id, chat_type="channel", …)` và không truyền `thread_id` [H1].
2. **Session đang bị chia theo người, không chỉ theo mention.** **[Sự thật]** Thread không có `thread_id`, và `group_sessions_per_user` mặc định là `true`. Vì vậy khoá session (session key) có thêm user id của người gửi [H2][H3]. Hai người cùng @mention bot trong một thread sẽ rơi vào hai session khác nhau.
3. **Hai phương án "backfill qua Graph" (A) và "nghe thụ động" (B) cần cùng một quyền: RSC `ChannelMessage.Read.Group`.** **[Sự thật]** Microsoft ghi rõ: khi chưa có RSC, bot chỉ nhận **và chỉ đọc được qua Graph** những tin nhắn có @mention nó [M3 dòng 11]. Vì vậy không có cách "đọc cả thread" nào mà không cần RSC, trừ khi cấp quyền toàn tenant `ChannelMessage.Read.All`. Quyền đó không phù hợp nguyên tắc chỉ-cần-biết.
4. **Khi đã cấp RSC, Teams sẽ đẩy mọi tin nhắn kênh tới bot.** **[Sự thật]** `TEAMS_REQUIRE_MENTION` mặc định là `false` [H1 dòng 383–391; H4 dòng 45–47]. Nếu không bật cờ này, bot sẽ trả lời mọi tin nhắn. **Bật `TEAMS_REQUIRE_MENTION=true` là bước bắt buộc.**
5. **Các hook plugin có tài liệu (`pre_gateway_dispatch`, `pre_llm_call`) không đủ dữ liệu để làm việc này.** **[Sự thật]**
   - Adapter bỏ tin không mention *trước khi* gọi `handle_message` [H1 dòng 515–521], nên hook không thấy các tin đó.
   - `MessageEvent` được tạo không kèm `raw_message` [H1 dòng 542–544], nên hook không đọc được `channelData` hay root id.
   - Handler `on_message` của Hermes được đăng ký trước và không gọi `ctx.next()`. Trong Teams SDK, handler đăng ký sau chỉ chạy khi handler trước gọi `ctx.next()` [T1][T2]. Vì vậy `register_platform_handler("teams", …)` gần như chắc chắn không nhận được tin nhắn.
6. **[Khuyến nghị] Làm phương án C (lai), sao chép đúng mẫu Slack mà Hermes đã có:**
   - Đặt `thread_id` = id tin gốc của thread, để cả thread dùng chung một session và các thread không lẫn nhau.
   - Cấp RSC `ChannelMessage.Read.Group` và bật `TEAMS_REQUIRE_MENTION=true`.
   - Khi bot được mention: nếu thread chưa có session thì gọi Graph lấy toàn bộ thread (backfill). Nếu đã có session thì chỉ lấy phần mới sau mốc đã đọc (watermark).
   - Đưa phần lấy được vào `MessageEvent.channel_context`, giống `SlackAdapter._hydrate_thread_context` [H5].
   - Đóng gói thành **plugin người dùng đăng ký lại platform `teams`** bằng lớp con của `TeamsAdapter`. Registry chọn bản đăng ký sau cùng [H6]; khi trùng khoá plugin, thứ tự ưu tiên là project > user > bundled [H7 dòng 538–541]. **[Chưa kiểm chứng]** Bản ghi đè có thắng thật trên bản Hermes của anh hay không. Về lâu dài, nên gửi thành PR lên Hermes.
7. **Giai đoạn 0, chỉ cần đổi cấu hình** **[Khuyến nghị, làm ngay]**: đặt `group_sessions_per_user: false`. Khoá session sẽ bỏ phần user id [H2 dòng 752–755]. Khi đó mọi mention trong cùng một `conv.id` vào chung một session, và mỗi thread đã có `conv.id` riêng (`;messageid=`, xem mục 2.2).
   - Không cần viết mã.
   - Hạn chế 1: đây là cấu hình **toàn cục**, áp cho nhóm trên mọi nền tảng [H18 dòng 1076–1078].
   - Hạn chế 2: không giải quyết việc thiếu tin không có mention.
8. **Ước lượng công sức** **[Giả định]**: khoảng 1–2 ngày công cho bản chạy được (lớp con adapter + Graph client + test với một team thật). Thêm khoảng 0,5–1 ngày nếu cần xử lý tin sửa/xoá và tóm tắt thread dài.

---

## 2. Bên trong Hermes

### 2.1 Xác minh dự án

- **[Sự thật]** `NousResearch/hermes-agent` có thư mục `gateway/` (lõi nhắn tin), `plugins/platforms/<tên>/` (adapter từng nền tảng) và hệ plugin (`hermes_cli/plugins.py`) [H7].
- **[Sự thật]** Adapter Teams là plugin đi kèm `teams-platform`, tác giả "Aamir Jawaid", dùng Bot Framework qua SDK `microsoft-teams-apps` [H4]. `pyproject.toml` ghim phiên bản `microsoft-teams-apps==2.1.1` [H8].
- **[Chưa kiểm chứng]** Hệ thống của anh có dùng adapter này hay một bridge tự viết hay không (xem mục 6).

### 2.2 Tin nhắn Teams được ánh xạ vào session như thế nào

Luồng chính nằm trong `TeamsAdapter._on_message` [H1 dòng 498–544]:

| Bước | Hành vi | Dòng |
|---|---|---|
| 1 | Bỏ tin do chính bot gửi (`from.id` thuộc `bot_ids`) | 502–506 |
| 2 | Lọc trùng theo `activity.id` (`MessageDeduplicator`, 1000 phần tử) | 372, 507–509 |
| 3 | Lưu `ConversationReference` theo `conv.id` | 512–513 |
| 4 | Nếu `require_mention` bật và không phải chat 1-1: bỏ tin không mention bot, trừ tin trả lời một tin bot đã gửi (`reply_to_id in _sent_ids`) | 515–521 |
| 5 | Xoá thẻ `<at>…</at>` khỏi nội dung | 522–523 |
| 6 | `build_source(chat_id=conv.id, chat_type=_CHAT_TYPES[...], user_id=aad_object_id, …)`, **không có `thread_id`** | 524–533 |
| 7 | `handle_message(MessageEvent(text, source, message_id, media…))`, **không có `raw_message`** | 542–544 |

`_CHAT_TYPES = {"personal": "dm", "groupChat": "group", "channel": "channel"}` [H1 dòng 325].

**Khoá session** do `build_session_key` tạo [H2 dòng 722–768]:

```
agent:<profile>:<platform>:<chat_type>[:<chat_id>][:<thread_id>][:<user_id>]
```

- **[Sự thật]** Với nhóm hoặc kênh, user id chỉ được thêm khi `group_sessions_per_user` bật **và** tin nhắn không nằm trong thread. Trong thread, session dùng chung trừ khi bật `thread_sessions_per_user` [H2 dòng 750–755]. Giá trị mặc định: `group_sessions_per_user=True`, `thread_sessions_per_user=False` [H3 dòng 615, 637–638].
- **[Sự thật]** Adapter Teams không đặt `thread_id`. Vì vậy một thread kênh hiện có khoá dạng `agent:main:teams:channel:<conv.id>:<aad_user_id>`, tức **mỗi người một session**.
- **[Sự thật]** Với tin trong kênh, `conversation.id` có dạng `19:…@thread.skype;messageid=1481567603816` [M1 dòng 195]. **[Giả định]** Hậu tố `;messageid=` là id tin gốc, nên mỗi thread đã có `chat_id` riêng và không lẫn giữa các thread. Tuy nhiên Microsoft dặn **không** phân tích hay dùng `conversation.id` làm khoá lưu trữ. Thay vào đó nên dùng `channelData.channel.id` kết hợp `channelData.replyChain.id` [M2 dòng 36–55].
- **[Sự thật]** Khi gửi trả lời, Hermes dùng `chat_id` (tức `conv.id`, có `;messageid=`) để gửi qua `/activities` [H1 dòng 735–751]. Microsoft vẫn cho phép cách gửi này: "Existing integrations that already send L2 messages directly through `/activities` can continue using that route" [M2 dòng 282]. **Hệ quả cho thiết kế:** không được đổi `chat_id`, chỉ thêm `thread_id`. Đổi `chat_id` sang id kênh gốc sẽ làm bot đăng tin ở cấp trên cùng của kênh.
- **[Sự thật, ngoài phạm vi]** Hàm gửi độc lập cho cron kiểm tra `chat_id` bằng `^[A-Za-z0-9:@\-_.]+$` [H1 dòng 85, 209–210]. Ký tự `;` và `=` bị từ chối, nên cron không gửi được vào một thread.

### 2.3 Các nền tảng khác xử lý ngữ cảnh thread thế nào

**Slack, khuôn mẫu nên sao chép** [H5]:

- `_hydrate_thread_context` (dòng 4392–4455) có ba nhánh:
  - (a) Thread chưa có session: lấy toàn bộ thread và ảnh của tin gốc, rồi đặt watermark.
  - (b) Đã có session và có @mention: lấy phần delta sau watermark, bỏ qua cache.
  - (c) Đã có session, tin thường: mỗi tiến trình làm "rehydration sau restart" một lần.
- Ngữ cảnh lấy được chỉ đưa vào **lượt mới**. Lịch sử cũ không bị viết lại, để giữ prompt cache (dòng 4400–4405).
- `_fetch_thread_context` (dòng 5908–5965) gọi `conversations.replies` với `limit=30`, có cache TTL và backoff khi gặp lỗi 429 (dòng 5976–5996).
- `_format_thread_context` (dòng 5998–6052):
  - bỏ tin vừa kích hoạt lượt này, vì nó đã là chính lượt người dùng;
  - bỏ tin cũ hơn watermark;
  - xoá mention bot;
  - thêm khối đầu `[Thread context — prior messages in this thread (not yet in conversation history):]` … `[End of thread context]`;
  - gắn nhãn `[unverified]` cho người gửi không nằm trong danh sách cho phép.
- `_thread_context_line` (dòng 6054–6088): tin trước đó của chính bot được ghi `[assistant]`. Tên và nội dung đi qua `neutralize_untrusted_inline_text`, để chặn việc giả mạo tiêu đề kiểu "## SYSTEM".
- Watermark được lưu trong metadata của session (`session_store.get/set_session_metadata`, khoá `slack_thread_watermark:<channel>:<ts>`), nên vẫn còn sau khi restart (dòng 6334–6358; [H2 dòng 1159–1165]).

**Telegram, chế độ "observe" (tiền lệ cho phương án B)** [H9]:

- Tin nhóm không mention được ghi thẳng vào transcript của session nhóm dùng chung. Hàm dùng là `store.append_to_transcript(session_id, {"role": "user", "content": "[tên|id] …", "observed": True, "message_id": …})`, và tin này không được gửi tới agent (adapter dòng 5715–5737).
- Lượt có mention được chuyển sang source dùng chung (`shared_session=True`) và kèm nhãn người gửi (`telegram_group_observe.py` dòng 10–31).

**Discord / Telegram topics** **[Sự thật]**: cả hai truyền `thread_id` vào `build_source` (Discord `adapter.py` dòng 1748, 4594; Telegram dòng 713, 764). Nhờ vậy thread và topic có session dùng chung theo quy tắc mục 2.2.

**Đường dẫn ngữ cảnh chung** **[Sự thật]**:

- `MessageEvent.channel_context` là "History-backfilled channel context (missed under require_mention)" [H10 dòng 110–113].
- Runner nối `channel_context` *sau* nhãn người gửi: `f"{channel_context}\n\n[New message]\n{message_text}"` [H11 dòng 1510–1518].
- Trong session nhiều người, `label_shared_sender` thêm `[Tên]` vào đầu tin [H2 dòng 679–698].

### 2.4 Điểm móc (hook) cho plugin và mức phù hợp

| Điểm móc | Nó làm được gì | Dùng được cho bài toán này không |
|---|---|---|
| `pre_gateway_dispatch(event, gateway, session_store)` | Chạy một lần cho mỗi `MessageEvent` từ adapter, trước bước xác thực. Trả về `skip`, `rewrite` (chỉ thay `text`) hoặc `allow`. Có thể `await` [H12 dòng 1378–1413; H13 dòng 74–127] | **Một phần.** Runner đọc lại `source = event.source` sau hook [H11 dòng 223–226], và `SessionSource` là dataclass không frozen [H2 dòng 68–69]. Vì vậy **[Giả định]** sửa trực tiếp `event.source.thread_id` hoặc `event.channel_context` trong hook sẽ có tác dụng. Đây là cách không được hỗ trợ chính thức. Ngoài ra hook không có `channelData` (thiếu `raw_message`) và không thấy tin bị cổng mention loại bỏ. |
| `pre_llm_call` | Thêm `{"context": …}` vào tin người dùng của lượt hiện tại, không vào system prompt [H12 dòng 460, 704–731] | Phù hợp để thêm ngữ cảnh. Nhưng hook chỉ nhận `session_id`, `platform`, `sender_id`, `user_message`…, không có root id hay team id. Phải tự ánh xạ từ session. |
| `ctx.register_platform_handler("teams", factory)` | `factory(native_app, adapter)` chạy khi kết nối. `native` là `App` của Teams, dùng decorator `on_message`/`on_card_action` [H14 dòng 2417–2447; H15 dòng 2178–2208] | **Không dùng được cho `on_message`.** Handler của Hermes được đăng ký trước [H1 dòng 420–430] và không gọi `ctx.next()`. Router của SDK chạy handler theo chuỗi middleware [T1][T2]. Handler cho loại khác (`on_message_update`/`on_message_delete`) có thể chạy được **[Chưa kiểm chứng]**. |
| Đăng ký lại platform `teams` từ plugin người dùng | `platform_registry.register`: "last writer wins on name clash" [H6 dòng 431–449]. Plugin người dùng (`~/.hermes/plugins/`) được nạp sau plugin đi kèm [H7 dòng 1–9] | **Phù hợp nhất.** Lớp con của `TeamsAdapter` truy cập được `activity` đầy đủ. Cần kiểm chứng thứ tự nạp trên bản đang chạy. |
| PR vào adapter gốc | Thêm `thread_id` và backfill giống Slack | Tốt nhất về lâu dài. Không phải bảo trì bản fork. |

Thông tin hỗ trợ thêm:

- **[Sự thật]** Hermes đã có Graph client có retry. Client này tôn trọng `Retry-After`, thử lại khi gặp 401, 429 và 5xx [H16 dòng 120–171]. Nó xác thực app-only qua `MSGRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET` [H17 dòng 14–18], nằm trong plugin `teams_pipeline`.
- **[Sự thật]** Nén ngữ cảnh mặc định đang bật: `compression.enabled: true`, `threshold: 0.50`, `target_ratio: 0.20`, `protect_last_n: 20` [H18 dòng 687–758].

---

## 3. Phía Microsoft Teams

### 3.1 Cách nhận diện một thread kênh

- **[Sự thật]** Có hai cấp tin nhắn. L1 là tin cấp trên cùng (có thể làm gốc thread). L2 là tin trả lời trong thread, và mọi L2 cùng chung một gốc [M2 dòng 13–14].
- **[Sự thật]** Id kênh chứa thread nằm ở `channelData.channel.id` (hoặc `channelData.chat.id`). Id tin gốc nằm ở `channelData.replyChain.id`. Cả L1 và L2 đều mang `replyChain.id` khi thread được hỗ trợ [M2 dòng 36–42].
- **[Sự thật]** Microsoft khuyến nghị lưu dữ liệu theo thread bằng "containing chat or channel ID together with the L1 root message ID". Không được phân tích `conversation.id` [M2 dòng 53–55].
- **[Sự thật]** Cách phân loại L2: `replyChain.id` có mặt và `activity.id` khác root id (áp dụng cho `message`, `messageUpdate`, `messageDelete`) [M2 bảng "Identify the incoming activity's level"].
- **[Sự thật, rủi ro]** Các tính năng thread trong tài liệu này "are in preview" (tài liệu ghi ngày 2026-11-17) [M2 dòng 18].
- **[Sự thật, rủi ro]** SDK Python mà Hermes ghim là `2.1.1`. Ở bản này, model `ChannelData` **chưa có** trường `reply_chain`. Trường này (`reply_chain_info.py`) chỉ có từ `v2.2.x` [T3]. Model dùng `extra="allow"` [T4], nên **[Giả định]** khoá gốc `replyChain` vẫn đọc được qua `model_extra`.
- **[Khuyến nghị]** Thứ tự dự phòng để lấy root id:
  1. `channel_data.reply_chain.id`
  2. `channel_data.model_extra["replyChain"]["id"]`
  3. phần sau `;messageid=` trong `conversation.id` (cách làm thực tế quen thuộc, nhưng Microsoft không khuyến khích)
  4. `activity.id` nếu là L1
- **[Sự thật]** Trường `replyToId` có trong activity [M1 dòng 218]. Theo Graph, `replyToId` của chatMessage là "ID of the parent chat message or root chat message of the thread" (chỉ áp dụng cho kênh) [M6 dòng 86].

### 3.2 Bot có nhận tin không mention không

- **[Sự thật]** Mặc định bot trong nhóm và kênh "only receive messages when they're directly @mentioned". Bot không nhận tin khi người dùng trả lời tin của bot mà không mention [M1 dòng 15].
- **[Sự thật]** RSC `ChannelMessage.Read.Group` cho bot "Receive all user messages in the channels of the team where the app is installed". `ChatMessage.Read.Chat` làm việc tương tự cho group chat [M3 dòng 24–27; M4 dòng 66, 102].
- **[Sự thật]** Chủ team phải đồng ý khi cài hoặc nâng cấp app [M3 dòng 54].
- **[Sự thật]** Quyền này cũng cho phép đọc qua Graph [M3 dòng 11]. Nó không mở thêm cuộc hội thoại nào, chỉ mở thêm tin nhắn trong những cuộc hội thoại bot đã tham gia [M3 dòng 17].
- **[Sự thật]** Teams **không** chuyển tin do bot hoặc agent khác viết, kể cả bot Workflows, dù đã có RSC. Giới hạn này chỉ áp dụng cho việc chuyển tin tới bot, không áp dụng khi đọc qua Graph [M3 dòng 13].
- **[Sự thật]** Kênh riêng tư (private channel): bot "can't post messages or Adaptive Cards in private channel conversations" [M1 dòng 20].

Ví dụ manifest [M3 dòng 31–51]:

```json
"webApplicationInfo": { "id": "<MICROSOFT-ENTRA-APP-ID>", "resource": "https://RscBasedStoreApp" },
"authorization": { "permissions": { "resourceSpecific": [
  { "name": "ChannelMessage.Read.Group", "type": "Application" }
]}}
```

### 3.3 Graph API để đọc thread

| Mục | Chi tiết | Nguồn |
|---|---|---|
| Lấy tin gốc | `GET /teams/{team-id}/channels/{channel-id}/messages/{message-id}`. API "list replies" **không** trả về tin gốc | [M5 dòng 15–17] |
| Lấy tin trả lời | `GET /teams/{team-id}/channels/{channel-id}/messages/{message-id}/replies`, `$top` tối đa 50, phân trang | [M5 dòng 36–41] |
| Quyền (application) | Ít quyền nhất: `ChannelMessage.Read.Group` (RSC). Quyền cao hơn: `ChannelMessage.Read.All`, `Group.Read.All`, `Group.ReadWrite.All` | [M5 dòng 23–30; M5b] |
| `team-id` | Là **Entra group id**, không phải `19:…` trong `channelData.team.id`. Bot lấy được id này qua API team details (`ctx.api.teams.get_by_id(team_id)`) | [M7 dòng 228]; [M5 dòng 68] (ví dụ id kênh dạng `19:…@thread.tacv2`) |
| Trường hữu ích | `createdDateTime`, `lastEditedDateTime`, `deletedDateTime`, `messageType` (`message`/`systemEventMessage`/…), `body` (HTML nếu có mention), `mentions`, `attachments`, `from` | [M6 dòng 67–86] |
| Giới hạn tốc độ | `GET …/channels/{id}/messages`: 20 rps cho mỗi app trong mỗi tenant, **1 rps cho mỗi kênh**. `GET …/replies` không có dòng riêng nên áp mức mặc định: GET 30 rps cho mỗi app trong mỗi tenant, 1 rps cho mỗi chat hoặc kênh. Mức duy trì khoảng 83% trong cửa sổ dài; nên dùng backoff | [M8 dòng 9–24, 48–54, 143–151] |
| Chi phí | "Starting August 25, 2025, the Teams APIs are no longer metered, and no billing configuration is required" | [M9 dòng 14] |
| Change notifications | Đăng ký `/teams/{id}/channels/{id}/messages` với `ChannelMessage.Read.Group` (RSC) hoặc `.All`. Có thể kèm dữ liệu tin. Subscription quá 1 giờ cần `lifecycleNotificationUrl`. `/teams/getAllMessages` cần `ChannelMessage.Read.All` (toàn tenant) | [M10 dòng 19, 27–35, 87–99] |

- **[Chưa kiểm chứng]** Trước đây Graph có danh sách "protected APIs" cho Teams (phải xin duyệt để đọc tin). Em không tải được trang này: đường dẫn cũ `concepts/teams-protected-apis.md` không còn trong kho tài liệu. Cần kiểm tra trong tenant thật xem lệnh `GET …/replies` bằng token app-only có bị từ chối với lỗi 403 hay không.
- **[Giả định]** `activity.id` của Bot Framework trùng với `id` của chatMessage trong Graph (cả hai đều là chuỗi số kiểu `1481567603816`). Việc lọc trùng dựa trên điều này, nên cần kiểm chứng.

---

## 4. So sánh phương án thiết kế

Điểm chung cho cả ba phương án:

- `thread_id = root id`, `chat_id` giữ nguyên.
- Lọc trùng theo message id.
- Bật `TEAMS_REQUIRE_MENTION=true`.

| Tiêu chí | A. Backfill khi được gọi (Graph) | B. Nghe thụ động (RSC) | C. Lai (khuyến nghị) |
|---|---|---|---|
| Độ đầy đủ | Cao. Có cả tin gửi lúc gateway tắt, tin gốc trước khi cài app, và phiên bản đã sửa mới nhất | Chỉ có tin tới sau khi cài RSC **và** lúc gateway đang chạy. Không biết tin sửa hoặc xoá nếu không xử lý thêm | Cao, như A |
| Độ sạch | Tốt. Chỉ thêm vào lượt mới, có khối đầu và cuối rõ ràng | Rủi ro. Tạo session cho **mọi** thread, kể cả thread bot không bao giờ được gọi. Transcript phình theo tin chuyện phiếm | Tốt. Session chỉ sinh khi có mention |
| Chi phí | 1–3 lệnh Graph mỗi lần mention (gốc + trả lời + trang tiếp). Không mất phí đo đếm [M9]. Giới hạn 1 rps mỗi kênh [M8] làm câu trả lời chậm thêm khoảng 1 giây cho mỗi lệnh gọi sau lệnh đầu. Thread 200 tin (gốc + 4 trang) chậm thêm khoảng 4 giây. Cache tin gốc sẽ bớt được 1 lệnh | Không gọi Graph, nhưng lưu và xử lý mọi tin | Như A. Có thể thêm cache trong bộ nhớ để giảm lệnh gọi |
| Độ phức tạp | Trung bình: Graph client, ánh xạ team id, chuyển HTML sang văn bản, watermark | Trung bình: ghi transcript, xử lý sửa/xoá, đồng bộ sau restart | Trung bình (A cộng ít logic) |
| Quyền cần có | RSC `ChannelMessage.Read.Group` (bắt buộc [M3 dòng 11]) và client secret của app | RSC `ChannelMessage.Read.Group` | RSC `ChannelMessage.Read.Group` |
| Riêng tư, chỉ-cần-biết | Chỉ đọc thread nơi bot được gọi | Lưu mọi tin của mọi kênh trong team | Chỉ đọc thread nơi bot được gọi |
| Tiền lệ trong Hermes | Slack `_hydrate_thread_context` [H5] | Telegram observe [H9] | Slack |

**[Khuyến nghị] Chọn C ở dạng "A có watermark, giống Slack".** RSC đằng nào cũng phải cấp. Tin không mention mà RSC đẩy tới bị cổng `require_mention` bỏ qua. Graph là nguồn sự thật duy nhất cho nội dung thread. Có thể thêm chế độ "observe" kiểu B về sau, chỉ cho thread đã có session, nếu cần thời gian thực hơn.

### 4.1 Các vấn đề chi tiết

- **Khoá session.** Đặt `source.thread_id = root_id`. Khoá sẽ là `agent:main:teams:channel:<conv.id>:<root_id>`, không có user id vì thread dùng chung theo mặc định [H2 dòng 750–755]. Nhãn `[Tên]` vẫn được thêm cho từng người [H2 dòng 679–698]. **[Giả định]** Các session kênh Teams hiện có sẽ bị "mồ côi" vì khoá đổi. Lần mention đầu tiên sau khi đổi sẽ backfill lại từ Graph.
- **Lọc trùng.**
  - Bỏ tin đang kích hoạt lượt (`id == activity.id`), vì nó đã là lượt người dùng.
  - Bỏ tin `createdDateTime <= watermark`.
  - Bỏ tin của chính bot (`from.application.id == app id`) khi session đã có, vì tin đó đã nằm trong lịch sử dưới dạng lượt assistant.
  - Khi chưa có session (lần đầu), giữ tin của bot với nhãn `[assistant]`, giống Slack [H5 dòng 6072–6075].
- **Thứ tự.** Sắp xếp phía client theo `createdDateTime`. Tài liệu không nêu thứ tự trả về của `/replies` [M5].
- **Tin sửa/xoá.**
  - Bỏ tin có `deletedDateTime` khác null.
  - Với tin cũ hơn watermark nhưng có `lastEditedDateTime > watermark`, thêm một dòng `[edited] Tên: …`.
  - Không sửa lại lịch sử cũ, để giữ prompt cache, đúng nguyên tắc Slack [H5 dòng 4400–4405].
- **Ghi tên người nói.** Dùng `from.user.displayName`. Đưa tên và nội dung qua `neutralize_untrusted_inline_text` [H2 dòng 295]. Gắn `[unverified]` cho người không thuộc `TEAMS_ALLOWED_USERS`, như Slack [H5 dòng 6079–6087].
- **Làm sạch nội dung.**
  - Chuyển HTML sang văn bản.
  - Xoá `<at>` của bot; giữ tên người được mention.
  - Thay tệp đính kèm bằng `[tệp: tên]`.
  - Thay Adaptive Card bằng `[card]`, hoặc bỏ thẻ approval do bot tạo.
  - Bỏ `messageType != "message"` (sự kiện hệ thống).
  - Bỏ chuỗi rỗng.
- **Ngân sách token.**
  - Giới hạn backfill lần đầu khoảng 40 tin hoặc 12.000 ký tự (con số đề xuất, cần chỉnh theo model).
  - Luôn giữ tin gốc cộng K tin mới nhất. Phần giữa thay bằng `[… N tin trước đã lược bớt …]`.
  - Sau đó để cơ chế nén của Hermes (`threshold 0.50`, `protect_last_n 20` [H18]) xử lý session dài.
  - Có thể tuỳ chọn tóm tắt phần giữa bằng LLM phụ, nhưng chưa cần lúc đầu.
- **Thread rất dài.**
  - Đọc phân trang `$top=50` theo `@odata.nextLink`.
  - Đặt trần số trang (ví dụ 4 trang, khoảng 200 tin), rồi áp ngân sách token ở trên.
  - Tôn trọng giới hạn 1 rps cho mỗi kênh [M8].
- **Riêng tư.**
  - RSC cho đọc mọi kênh **của team đã cài app** [M3 dòng 26].
  - Chỉ gọi Graph khi có mention, và chỉ cho đúng thread đó.
  - Không ghi nội dung tin vào log.
  - Thời gian lưu session theo `session_store_max_age_days` (mặc định 90 ngày [H3 dòng 677]).
  - Microsoft yêu cầu mô tả app phải nói rõ cách dùng dữ liệu tin nhắn nếu phát hành lên Store [M3, mục "Update the app description"].
- **Kênh riêng tư hoặc kênh chia sẻ.** Bot không đăng tin được vào kênh riêng tư [M1 dòng 20]. Nên bỏ qua loại kênh này.

---

## 5. Phác thảo triển khai

### 5.1 Cấu hình Azure / Teams

1. Trong manifest của app Teams, thêm `webApplicationInfo.id = <TEAMS_CLIENT_ID>` và `authorization.permissions.resourceSpecific: [{"name": "ChannelMessage.Read.Group", "type": "Application"}]` [M3].
2. Nâng cấp hoặc cài lại app trong team. Chủ team đồng ý quyền RSC [M3 dòng 54].
3. Trong cấu hình Hermes, đặt `TEAMS_REQUIRE_MENTION=true` **trước** khi nâng cấp app, để bot không trả lời mọi tin [H4 dòng 45–47].
4. Token Graph: client-credentials của chính app bot, scope `https://graph.microsoft.com/.default` [H17 dòng 14]. Không cần cấp quyền Graph toàn tenant.
   - Graph client của `teams_pipeline` đọc `MSGRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET` [H17 dòng 18], không đọc `TEAMS_*`.
   - Nếu dùng lại client này: đặt `MSGRAPH_*` bằng đúng giá trị `TEAMS_*` của app bot. Hoặc tạo token provider từ `self._client_id/_client_secret/_tenant_id` của adapter.
   - **[Giả định]** Quyền RSC gắn với chính app id khai báo trong `webApplicationInfo.id`. Vì vậy phải dùng **cùng** app đó để lấy token.
5. Plugin người dùng là opt-in: phải thêm khoá plugin vào `plugins.enabled` [H7 dòng 531].

### 5.2 Plugin `~/.hermes/plugins/teams-threads/`

Gồm `plugin.yaml` (`kind: platform`) và `__init__.py` với `register(ctx)`. **[Giả định]** Đường import `plugins.platforms.teams.adapter` dùng được; adapter Telegram import theo kiểu này [H9 dòng 219].

```python
# __init__.py — pseudo-code, chưa chạy thử
import asyncio, contextvars, html, re
from plugins.platforms.teams import adapter as base  # bản đi kèm

_ACT = contextvars.ContextVar("teams_activity", default=None)
MAX_MSGS, MAX_CHARS, MAX_PAGES = 40, 12_000, 4

def _root_id(act):
    cd = getattr(act, "channel_data", None)
    rc = getattr(cd, "reply_chain", None) or (getattr(cd, "model_extra", None) or {}).get("replyChain")
    rid = (rc.get("id") if isinstance(rc, dict) else getattr(rc, "id", None)) if rc else None
    if not rid:  # dự phòng: Microsoft khuyên không phân tích conversation.id [M2]
        m = re.search(r";messageid=(\d+)", getattr(act.conversation, "id", "") or "")
        rid = m.group(1) if m else None
    return rid or getattr(act, "id", None)  # tin L1 là gốc của chính nó

class ThreadAwareTeamsAdapter(base.TeamsAdapter):
    async def _on_message(self, ctx):
        token = _ACT.set(ctx.activity)
        try:
            await super()._on_message(ctx)          # giữ nguyên cổng mention, lọc trùng, media
        finally:
            _ACT.reset(token)

    def build_source(self, **kw):
        act = _ACT.get()
        if act is not None and kw.get("chat_type") == "channel" and not kw.get("thread_id"):
            kw["thread_id"] = _root_id(act)         # GIỮ chat_id = conv.id để trả lời đúng thread
        return super().build_source(**kw)

    async def handle_message(self, event):
        act = _ACT.get()
        src = event.source
        if act is not None and src.chat_type == "channel" and src.thread_id:
            try:
                event.channel_context = await self._thread_backfill(act, src, event.message_id)
            except Exception:
                pass                                 # fail-open: vẫn trả lời, chỉ thiếu ngữ cảnh
        return await super().handle_message(event)

    async def _thread_backfill(self, act, src, current_id):
        key = self._source_session_key(src)                     # [H15 dòng 2431–2436]
        store = self._session_store
        wm_key = f"teams_thread_watermark:{src.thread_id}"
        # Watermark rỗng = cold start (chưa có session, HOẶC session vừa /new/auto-reset:
        # entry mới không mang metadata cũ [H2 dòng 1239–1248]). Không dùng "key in _entries".
        watermark = parse_dt(store.get_session_metadata(key, wm_key, "") or "")   # None nếu rỗng
        has_session = watermark is not None
        team_gid = await self._team_group_id(act.channel_data.team.id)   # cache; Bot API team details
        chan_id = act.channel_data.channel.id
        root = await self._root_cache.get_or_fetch(               # cache: gốc ít đổi
            src.thread_id, lambda: graph.get_json(f"/teams/{team_gid}/channels/{chan_id}/messages/{src.thread_id}"))
        await pace_channel(chan_id, min_interval=1.0)             # giới hạn 1 rps/kênh [M8]
        replies = await graph.collect_paginated(
            f"/teams/{team_gid}/channels/{chan_id}/messages/{src.thread_id}/replies",
            params={"$top": 50}, max_pages=MAX_PAGES)
        msgs = sorted([root, *replies], key=lambda m: parse_dt(m["createdDateTime"]))  # so sánh datetime, không so chuỗi
        lines = []
        for m in msgs:
            if m["id"] == current_id or m.get("deletedDateTime") or m.get("messageType") != "message":
                continue
            created, edited_at = parse_dt(m["createdDateTime"]), parse_dt(m.get("lastEditedDateTime"))
            new = not has_session or created > watermark
            edited = has_session and not new and edited_at is not None and edited_at > watermark
            if not (new or edited):
                continue
            is_self = (m.get("from") or {}).get("application", {}).get("id") == self._client_id
            if is_self and has_session:
                continue                                # đã có trong lịch sử dưới dạng lượt assistant
            lines.append(fmt_line(m, is_root=m["id"] == src.thread_id, is_self=is_self, edited=edited))
        lines = budget(lines, MAX_MSGS, MAX_CHARS)      # giữ tin gốc + phần cuối, chèn "[… N tin lược bớt …]"
        newest = max((m.get("lastEditedDateTime") or m["createdDateTime"] for m in msgs), key=parse_dt)
        # set_session_metadata là no-op khi entry CHƯA tồn tại (_update_entry trả False
        # [H19]) — lần mention đầu tiên entry chưa có. Tạo entry trước, như Telegram observe
        # [H9 dòng 5724], rồi mới ghi; chạy trong worker như Slack [H5 dòng 6345–6358].
        await asyncio.to_thread(store.get_or_create_session, src)
        await asyncio.to_thread(store.set_session_metadata, key, wm_key, newest)
        if not lines:
            return None
        return ("[Teams thread context — prior messages in this thread (not yet in conversation history):]\n"
                + "\n".join(lines) + "\n[End of thread context]\n")

def register(ctx):
    ctx.register_platform(
        name="teams", label="Microsoft Teams",
        adapter_factory=lambda cfg: ThreadAwareTeamsAdapter(cfg),
        check_fn=base.check_requirements, ensure_deps_fn=base.check_teams_requirements,
        validate_config=base.validate_config, is_connected=base.is_connected,
        required_env=["TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID"],
        allowed_users_env="TEAMS_ALLOWED_USERS", allow_all_env="TEAMS_ALLOW_ALL_USERS",
        standalone_sender_fn=base._standalone_send, max_message_length=28000,
        cron_deliver_env_var="TEAMS_HOME_CHANNEL",
        platform_hint="…giữ như bản gốc…")             # sao chép đủ các trường ở [H1 dòng 866–886]
```

Lưu ý về đoạn mã trên:

- `fmt_line` làm các việc: chuyển HTML sang văn bản, xoá `<at>` của bot, thêm nhãn `[thread parent]`, `[assistant]` hoặc `[edited]`, gắn `[unverified]` qua `self._is_sender_authorized(...)`, và gọi `neutralize_untrusted_inline_text`.
- `collect_paginated` có sẵn trong Graph client của `teams_pipeline` [H16 dòng 68]. Hàm gốc không có tham số `max_pages`; tham số này trong pseudo-code là phần cần tự thêm. Có thể dùng lại client đó hoặc tự viết bằng `httpx`.
- `_ACT` (contextvar) cần thiết vì `_on_message` gọi `build_source` và `handle_message` mà không truyền `activity`.
  - **[Sự thật]** `_on_message` `await` trực tiếp `self.handle_message(...)` [H1 dòng 542]. Vì vậy hàm override chạy trong cùng task và đọc được contextvar.
  - Rủi ro còn lại: Hermes gom tin văn bản theo lô (`_enqueue_text_event` [H15 dòng 2442]). Hai tin gửi liền nhau có thể bị gộp làm một. Khi đó cần lấy `channel_context` của tin cuối.
- `parse_dt`, `pace_channel`, `_root_cache`, `_team_group_id`, `fmt_line`, `budget` là các hàm phụ trợ cần tự viết.
- Phương án dự phòng không cần fork: một hook `pre_gateway_dispatch` gán `event.source.thread_id` và `event.channel_context`. Hook này vẫn cần root id, nên phải phân tích `;messageid=` từ `source.chat_id`. Cách này dựa vào hành vi không được hỗ trợ (sửa đối tượng tại chỗ). **[Khuyến nghị]** Chỉ dùng tạm.

### 5.3 Tiêu chí chấp nhận (cách kiểm tra là xong)

1. Người A mention bot ở tin gốc, người B mention bot ở tin trả lời: cả hai vào **cùng** một session. Kiểm tra bằng khoá session trong `sessions.json` hoặc `state.db`.
2. Có 5 tin không mention trước tin mention: agent thấy cả 5 tin, đúng thứ tự, có tên người nói. Không lặp lại tin đang kích hoạt lượt.
3. Mention lần hai: chỉ có tin mới sau watermark được thêm vào. Không lặp lại tin của bot.
4. Mention trong thread X không để lộ nội dung thread Y.
5. Tin không mention không nhận được câu trả lời (`TEAMS_REQUIRE_MENTION=true`).
6. Thread 300 tin: ngữ cảnh thêm vào không vượt ngân sách ký tự. Không gặp lỗi 429 hoặc đã xử lý được bằng backoff.

---

## 6. Câu hỏi mở (cần kiểm tra trên hệ thống thật)

1. Phiên bản Hermes đang chạy (`hermes --version`) và commit. Đang dùng adapter `teams` đi kèm hay một bridge tự viết? Nếu là bridge tự viết, mục 2 chỉ dùng làm tham khảo.
2. App đã có RSC `ChannelMessage.Read.Group` chưa? Hiện bot chỉ nhận tin có mention, nên nhiều khả năng là chưa.
3. Giá trị `group_sessions_per_user` / `thread_sessions_per_user` hiện tại. Có profile hay `profile_routes` nào ảnh hưởng tới khoá không?
4. Payload thật của một tin L1 và một tin L2: có `channelData.replyChain.id` không? `conversation.id` có hậu tố `;messageid=<root>` cho cả L1 và L2 không? `activity.id` có trùng `id` của tin trong Graph không?
5. `GET …/messages/{id}/replies` bằng token app-only kèm RSC có chạy trong tenant này không, hay bị lỗi 403 vì quy định protected API (em chưa kiểm chứng được)?
6. Thứ tự nạp plugin: plugin người dùng đăng ký lại `teams` có thắng bản đi kèm không? Kiểm tra log `Platform 'teams' re-registered`.
7. Có dùng kênh riêng tư hoặc kênh chia sẻ không? Có cần đọc tin của bot khác không? Teams không chuyển tin đó tới bot, nhưng Graph vẫn đọc được.
8. Có chấp nhận việc các session kênh Teams hiện có bị "mồ côi" khi khoá đổi không?
9. Có muốn gửi PR lên `NousResearch/hermes-agent` thay vì giữ bản fork không?

---

## 7. Nguồn

**Hermes Agent**: permalink tại commit `9f47677483d1b4de4d1a88d37b3b7ca18b18b215`. Mỗi liên kết trỏ tới đoạn chính; các dòng khác được ghi kèm và dùng cùng tệp.

- [H1] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/platforms/teams/adapter.py#L498-L554 (các dòng khác: 85, 209–210, 325, 350–391, 420–430, 735–751, 866–886)
- [H2] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/session.py#L722-L768 (các dòng khác: 68–78, 295, 666–698, 1159–1172, 1239–1248)
- [H3] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/config.py#L613-L638 (dòng 677)
- [H4] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/platforms/teams/plugin.yaml#L45-L47
- [H5] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/platforms/slack/adapter.py#L4392-L4455 (các dòng khác: 5908–6088, 6278–6380)
- [H6] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/platform_registry.py#L431-L449 (phạm vi theo profile: dòng 38–43, 233–243, 480–490)
- [H7] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/hermes_cli/plugins.py#L113-L213 (các dòng khác: 1–9, 145, 530–547)
- [H8] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/pyproject.toml#L440
- [H9] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/platforms/telegram/adapter.py#L5715-L5737 (các dòng khác: 219, 713, 764); https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/platforms/telegram/telegram_group_observe.py#L10-L31
- [H10] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/platforms/event.py#L67-L120
- [H11] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/run_inbound.py#L1510-L1518 (dòng 150–226)
- [H12] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/website/docs/user-guide/features/hooks.md#L1378-L1413 (các dòng khác: 460, 486, 704–731)
- [H13] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/run_inbound_hooks.py#L74-L127
- [H14] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/website/docs/developer-guide/plugins/index.md#L2417-L2461
- [H15] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/platforms/base.py#L2178-L2208 (các dòng khác: 2431–2446, 4587–4627)
- [H16] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/teams_pipeline/graph_client.py#L50-L171
- [H17] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/plugins/teams_pipeline/graph_auth.py#L14-L18
- [H18] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/cli-config.yaml.example#L687-L758 (dòng 1076–1078)
- [H19] https://github.com/NousResearch/hermes-agent/blob/9f47677483d1b4de4d1a88d37b3b7ca18b18b215/gateway/session_lifecycle.py#L163-L171 (`_update_entry` trả `False` khi entry chưa tồn tại)

**Microsoft Teams SDK cho Python** (bản Hermes ghim là `v2.1.1`):

- [T1] https://github.com/microsoft/teams.py/blob/v2.1.1/packages/apps/src/microsoft_teams/apps/routing/router.py#L23-L33
- [T2] https://github.com/microsoft/teams.py/blob/v2.1.1/packages/apps/src/microsoft_teams/apps/app_process.py#L426-L472 (dòng 285: chọn handler; nhánh `main` có cùng logic ở dòng 452–497)
- [T3] https://github.com/microsoft/teams.py/tree/v2.1.1/packages/api/src/microsoft_teams/api/models/channel_data so với https://github.com/microsoft/teams.py/tree/v2.2.1/packages/api/src/microsoft_teams/api/models/channel_data (chỉ `v2.2.1` có `reply_chain_info.py`)
- [T4] https://github.com/microsoft/teams.py/blob/v2.1.1/packages/api/src/microsoft_teams/api/models/custom_base_model.py#L41-L46

**Microsoft Learn** (đọc từ nguồn trên GitHub; dòng tính theo file `.md`):

- [M1] Channel and group chat conversations: https://learn.microsoft.com/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations (nguồn: `MicrosoftDocs/msteams-docs/msteams-platform/bots/how-to/conversations/channel-and-group-conversations.md`, dòng 15–20, 195, 218)
- [M2] Use threads in channels and one-on-one agent chats: https://learn.microsoft.com/microsoftteams/platform/bots/how-to/conversations/agent-chat-threads (`…/agent-chat-threads.md`, dòng 13–55, 263–310; ngày 2026-11-17; preview)
- [M3] Configure an agent to receive all messages: https://learn.microsoft.com/microsoftteams/platform/agents-in-teams/enable-receive-all-chat-messages (`…/agents-in-teams/enable-receive-all-chat-messages.md`, dòng 11–77)
- [M4] Resource-specific consent: https://learn.microsoft.com/microsoftteams/platform/graph-api/rsc/resource-specific-consent (`…/graph-api/rsc/resource-specific-consent.md`, dòng 66, 102)
- [M5] List replies: https://learn.microsoft.com/graph/api/chatmessage-list-replies (`microsoft-graph-docs-contrib/api-reference/v1.0/api/chatmessage-list-replies.md`, dòng 15–41, 68); [M5b] bảng quyền `api-reference/v1.0/includes/permissions/chatmessage-list-replies-permissions.md`
- [M6] chatMessage resource: https://learn.microsoft.com/graph/api/resources/chatmessage (`api-reference/v1.0/resources/chatmessage.md`, dòng 67–86)
- [M7] Get Teams context: https://learn.microsoft.com/microsoftteams/platform/bots/how-to/get-teams-context (`…/get-teams-context.md`, dòng 228–300)
- [M8] Microsoft Teams service limits: https://learn.microsoft.com/graph/throttling-limits#microsoft-teams-service-limits (`includes/throttling-teams.md`, dòng 9–24, 48–54, 143–151)
- [M9] Metered APIs list: https://learn.microsoft.com/graph/metered-api-list (`concepts/metered-api-list.md`, dòng 14)
- [M10] Change notifications for Teams messages: https://learn.microsoft.com/graph/teams-changenotifications-chatmessage (`concepts/teams-changenotifications-chatmessage.md`, dòng 19–99)

### Những điểm chưa kiểm chứng (tổng hợp)

- Hậu tố `;messageid=` có trên cả L1 và L2. `activity.id` trùng id trong Graph.
- `replyChain` đọc được qua `model_extra` trên SDK 2.1.1.
- Trạng thái "protected API" của endpoint `/replies` (tài liệu không tải được).
- Thứ tự nạp và ghi đè plugin `teams` trên bản Hermes của anh. Hành vi contextvar khi có gom lô văn bản.
- Handler `on_message_update`/`on_message_delete` từ plugin có chạy được hay không.
- Tin của bot trong Graph có `from.application.id` bằng app id của bot hay không. Việc lọc trùng tin của bot dựa vào điều này.
- `get_or_create_session` gọi từ adapter trước khi dispatch có tác dụng phụ không mong muốn hay không. Telegram observe đã làm vậy, nhưng chưa thử với Teams.
- Không có đoạn mã nào ở mục 5 đã được chạy thử. Đó là pseudo-code.

## 8. Kiểm tra trên máy `solution` (2026-11-20, chỉ đọc)

- Hermes: bản cài từ `NousResearch/hermes-agent`, commit `ae2a81ccc0` (2026-11-16), không có thay đổi cục bộ (`git status` sạch).
- Dùng bộ kết nối Teams có sẵn: `~/.hermes/hermes-agent/plugins/platforms/teams/`. Plugin người dùng hiện có: `composio-connections`, `hermes-push`; chưa có plugin Teams nào.
- `config.yaml:1084` đã đặt `group_sessions_per_user: false` → vấn đề "tách session theo người" (mục 1) **đã được xử lý trên máy này**. Theo `gateway/session.py:752-765`, khi cờ này là false thì khóa session không chứa ID người gửi.
- `.env` chưa có `TEAMS_REQUIRE_MENTION` → mặc định false (`adapter.py:383-385`). Phải đặt `TEAMS_REQUIRE_MENTION=true` trước khi bật RSC.
- `sessions/sessions.json` chỉ có 2 session Teams (1 DM, 1 group chat); chưa có session nào của channel (`@thread.tacv2`). Vì vậy chưa xác nhận được khóa session của channel có chứa `;messageid=` hay không.
- `hermes_cli/plugins.py:3,538`: nguồn plugin sau ghi đè nguồn trước khi trùng khóa (project > user > bundled), trừ trường hợp "flat impostor". Cần thử thực tế để chắc plugin người dùng thay được platform `teams`.
