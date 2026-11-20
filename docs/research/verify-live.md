# Kiểm chứng trên tenant thật (2026-11-20)

Môi trường: máy `solution`, Hermes commit `ae2a81ccc0`, app Teams `Conan (TuanHV)` (`2f0245c5-26bd-4d37-906c-800b18b06982`) bản `1.0.1`, team `2670e8c8-f13b-4984-8c3c-da029555ef61`, channel `sol-personal-tuanhv` (`19:G0zfmGO8hphznutt07o4Dl6tUAMSRmdaPJjKay0_Qt81@thread.tacv2`).

## Thay đổi đã làm (đã được anh Tuấn duyệt)

| Thay đổi | Lệnh | Cách gỡ |
|---|---|---|
| `TEAMS_REQUIRE_MENTION=true` trong `~/.hermes/.env` (dòng 559) + restart `hermes-gateway.service` | `systemctl --user restart hermes-gateway.service` | `cp -p ~/.hermes/.env.bak-20261120-require-mention ~/.hermes/.env` rồi restart |
| Thêm RSC `ChannelMessage.Read.Group` (Application), version tự nâng `1.0.0 → 1.0.1` | `teams app rsc add 2f0245c5-… ChannelMessage.Read.Group --type Application` | `teams app rsc remove 2f0245c5-… ChannelMessage.Read.Group` |

## Kết quả

| Mục | Kết luận | Bằng chứng |
|---|---|---|
| RSC có hiệu lực, token app-only đọc được thread | **Đúng** | `GET /teams/{G}/channels/{C}/messages?$top=10` → 200; `GET …/messages/1795145910429/replies` → 200. Token chỉ có role `Group.Selected`, nên quyền đọc đến từ RSC. |
| `conversation.id` của tin **trả lời** có `;messageid=<root>` | **Đúng** | Tin mention là một reply; log `gateway.log` 03:38:57: `chat=19:G0zf…@thread.tacv2;messageid=1795145910429`, session key `agent:main:teams:channel:…;messageid=1795145910429`. |
| `messageid` trong `conversation.id` = id tin gốc trong Graph | **Đúng** | Graph root id `1795145910429` trùng giá trị trong `conversation.id`. |
| Mỗi thread đã có session riêng (không cần đổi khóa session) | **Đúng** cho tin reply | Như trên. Chưa thử trường hợp mention ngay ở tin gốc. |
| `activity.id` của tin mention = Graph message id | **Chưa rõ** | Log không ghi `activity.id`. Định dạng giống nhau (số mili-giây). Cần log thêm khi viết plugin. |
| Nhận diện tin của bot trong Graph | **Đúng** | Reply của bot: `from.application.id = 2f0245c5-…`, `applicationIdentityType = bot`, `from.user = null`. |
| Tin không mention có tới gateway (RSC push) | **Chưa rõ** | Adapter chỉ log việc bỏ tin ở mức DEBUG. Không ảnh hưởng phương án đọc bù qua Graph. |
| Sửa / xoá tin | **Chưa thử** | Thread thử không có tin sửa/xoá. |

## Minh hoạ đúng vấn đề cần giải

Thread `1795145910429`:

1. Gốc: "có thấy tin này không?" (không mention)
2. Reply: "haha em thấy tin này thì chào anh bằng 3 thứ tiếng nhé" (không mention)
3. Reply: "@Conan (TuanHV)" (chỉ có mention) → gateway nhận `msg_len=0`
4. Bot trả lời chung chung: "Dạ em chào anh Tuấn, em đã nhận được tin nhắn. Anh cần em hỗ trợ việc gì ạ?"

Bot không thấy yêu cầu ở tin 2. Đọc bù thread qua Graph sẽ đưa tin 1–2 vào ngữ cảnh.

Token tạm (`/tmp/hv-graph` trên `solution`) đã xoá sau khi thử.

## Lần thử 2 (03:43–03:44 UTC)

Anh Tuấn: mention bot ở tin gốc của thread mới; sửa tin gốc `1795145902383`; xoá tin gốc `1795145910429` (thread thử lần 1).

| Mục | Kết luận | Bằng chứng |
|---|---|---|
| Mention ở **tin gốc**: `conversation.id` có `;messageid=<id của chính tin gốc>` | **Đúng** | `gateway.log` 03:43:51: `session=agent:main:teams:channel:…;messageid=1795146225973`; Graph: tin gốc có id `1795146225973`. |
| Cổng C1: tin gốc và tin trả lời cùng khoá session | **Đúng (suy ra từ 2 lần thử)** | Tin trả lời dùng id tin gốc (lần 1), tin gốc dùng id của chính nó (lần 2) → cùng một giá trị. Chưa thử cả hai trong **cùng** một thread. |
| Nội dung mention ở tin gốc | Bot nhận `hello` (`msg_len=5`), thẻ `<at>` đã được bỏ | `agent.log` 03:43:47 |
| Tin bị sửa trong Graph | Có `lastEditedDateTime`, `body` là nội dung mới | `1795145902383`: `lastEditedDateTime=2026-11-20T03:43:59.955Z` |
| Tin gốc bị xoá trong Graph | Vẫn trả về trong `/messages`, có `deletedDateTime`, `body` rỗng; các tin trả lời vẫn đọc được | `1795145910429`: `deletedDateTime=2026-11-20T03:43:53.115Z` |
| Bot có nhận sự kiện sửa/xoá | **Không thấy trong log** | `gateway.log`, `agent.log`, `errors.log` không có dòng nào sau 03:43:51. Có thể adapter bỏ qua im lặng. |

## Lần thử 3 — cổng C2 (04:00 UTC, plugin `teams-threads` v0.0.1)

Anh Tuấn trả lời "oke luôn" (Graph: `mentions=[]`, **không** mention) vào thread `1795146225973`.

| Mục | Kết luận | Bằng chứng |
|---|---|---|
| Teams đẩy tin **không mention** tới bot khi có RSC | **Đúng** | `aiohttp.access` 04:00:17 `POST /api/messages 200`; log plugin `[teams-threads] inbound {...}` |
| Bộ lọc mention gốc bỏ tin đó | **Đúng** | Không có dòng `gateway.run: inbound message` / `response ready` sau 04:00:17 |
| `activity.id` = Graph message id | **Đúng** | `activity_id=1795147215633` = Graph id `1795147215633` |
| `channelData.replyChain.id` có trong activity | **Đúng** | `reply_chain_id=1795146225973` (= id tin gốc) |
| `channelData.team.aadGroupId` có trong activity | **Đúng** | `team_aad_group_id=2670e8c8-…` |
| `reply_to_id` của tin trả lời trong channel | **`None`** | Ngoại lệ `_sent_ids` của adapter gốc không bao giờ áp dụng trong channel |

## Lần thử 4 — plugin v0.1.2 chạy thật (2026-11-20, 05:22–05:32 UTC)

Deploy: bản khung v0.0.1 sao lưu ở `~/.hermes/plugins-backup-teams-threads-v0.0.1`; gateway khởi động lại 05:22:45; `agent.log` có `registered ThreadAwareTeamsAdapter v0.1.2`.
Tin thử gửi bằng tài khoản Teams của anh Tuấn qua Composio (`composio proxy` tới Graph; công cụ `MICROSOFT_TEAMS_POST_MESSAGE_REPLY` không cho mention bot vì thiếu `applicationIdentityType: bot`).

| TC | Thread (root id) | Kết quả | Bằng chứng |
|---|---|---|---|
| TC1 | `1795152290200` | **Đạt** | Bot chào Việt / Nhật / Anh; log `mode=full fetched=3 added=2 budget=400000 tail=100000` |
| TC2 | `1795152355739` | **Đạt** | Lượt 2 `mode=delta added=0`; bot nhớ "Mèo" ở lượt 4 |
| TC3 | `1795152355739` | **Đạt** | Tin không mention: không có trả lời trong 40 s; lần mention sau: `added=1`, bot trả lời "4711" |
| TC4 | `1795152465402`, `1795152469295` | **Đạt** | Mỗi thread chỉ biết màu của mình (ĐỎ / XANH LÁ) |
| TC5 | `1795152471825` | **Đạt** | Không có trả lời; root id không xuất hiện trong `agent.log`/`gateway.log` (không gọi Graph) |
| TC6 | `1795152469295` | **Đạt** | Gateway tắt, gửi 2 tin, bật lại; lần mention sau `mode=delta added=2`, bot trả lời "Huế", "88" |
| TC7 | `1795152465402` | **Đạt** | Sau `/new`: `mode=full fetched=6 added=3`; bot vẫn trả lời "ĐỎ" |
| TC8 (phần tool) | `1795152469295` | **Đạt** | `agent.tool_executor: tool teams_thread_read completed (2.01s)`; bot đếm đúng 4 tin, 1 tin của bot |
| Tóm tắt bằng LLM phụ | — | **Đạt** | Gọi `async_call_llm(task="compression")` trực tiếp trên host: trả về bản tóm tắt tiếng Việt đúng nội dung |
| Ngân sách giống Hermes | — | **Đạt** | Cửa sổ 1.000.000 → session mới `budget=400000`; sau vài lượt `budget=482989` (= 500000 − `last_prompt_tokens`) |

Chưa thử thật: TC8 phần nén (cần thread > 400.000 token), TC8b, TC9, TC10 — chỉ có test tự động.
Dòng `capability_check ... tools.override decision=deny` xuất hiện cho cả `composio-connections`, `hermes-push`: là kiểm tra chung, không phải lỗi của plugin.

### TC10 (DM và group chat) — **Đạt**

- DM 05:34: tin tới bot, bot trả lời; không có dòng `[teams-threads]` (đi đường adapter gốc).
- Group chat mới (06:01): `msTeamsAppInstalled` (teamsAppId `b8140c67-…`), mention → `inbound message` → `response ready` sau 3 s; không có dòng `[teams-threads]`.
- Group chat cũ: app bị gỡ lúc 05:35:03 (`msTeamsAppRemoved`) khi anh Tuấn tải lại gói app; thêm lại không thành công, group mới thì được.
- Có **hai bản app tải lên riêng** cùng trỏ tới một bot `2f0245c5-…`: `c86cf3db-…` (trong team) và `b8140c67-…` (cài cho cá nhân, dùng cho chat). Cách cài đúng cho chat: link `teams app get` in ra; tải gói thủ công báo `AppDefinitionDoesntExist`.
