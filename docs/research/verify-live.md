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
