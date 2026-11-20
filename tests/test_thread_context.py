"""Pure logic: F1 root id, F7 filtering, F9 HTML, F10 format/order/dedup, F11 budget + map-reduce, F12 header."""
import asyncio
from datetime import datetime, timezone

import pytest

from teams_threads import thread_context as tc

BOT = "bot-app-id"
ROOT = "1000"
UTC = timezone.utc


def gm(mid, minute, text="hi", *, user="Tuấn", app=None, deleted=False, mtype="message",
       ctype="html", attachments=None):
    """A Graph chatMessage dict as returned by /messages/{id} and /replies."""
    sender = ({"user": None, "application": {"id": app, "displayName": "Conan",
                                             "applicationIdentityType": "bot"}}
              if app else {"user": {"displayName": user, "id": "u1"}, "application": None})
    return {
        "id": mid, "messageType": mtype,
        "createdDateTime": f"2026-11-20T03:{minute:02d}:00.123Z",
        "deletedDateTime": "2026-11-20T04:00:00Z" if deleted else None,
        "from": sender,
        "body": {"contentType": ctype, "content": "" if deleted else text},
        "attachments": attachments or [],
    }


# ---------- F1: root id fallback ----------

def test_root_id_prefers_reply_chain():
    assert tc.resolve_root_id("111", "19:c@thread.tacv2;messageid=222", "333") == ("111", "replyChain")


def test_root_id_falls_back_to_conversation_suffix():
    assert tc.resolve_root_id(None, "19:c@thread.tacv2;messageid=222", "333") == ("222", "conversation")


def test_root_id_falls_back_to_activity_id_for_root_message():
    assert tc.resolve_root_id(None, "19:c@thread.tacv2", "333") == ("333", "activity")


def test_root_id_not_taken_from_a_reply_activity():
    # F1: activity.id is the root only when the activity is the root post (no replyToId).
    assert tc.resolve_root_id(None, "19:c@thread.tacv2", "333", reply_to_id="111") == (None, "none")


def test_root_id_from_conversation_helper():
    assert tc.root_id_from_conversation("19:c@thread.tacv2;messageid=222") == "222"
    assert tc.root_id_from_conversation("a:1") is None


def test_channel_id_strips_messageid_suffix():
    assert tc.channel_id_from_conversation("19:c@thread.tacv2;messageid=222") == "19:c@thread.tacv2"


# ---------- F9: HTML -> text ----------

def test_html_mentions_entities_and_tags():
    html = '<p><at id="0">Conan (TuanHV)</at>&nbsp;chào <b>anh</b> &amp; em</p>'
    assert tc.html_to_text(html) == "@Conan (TuanHV) chào anh & em"


def test_html_keeps_line_breaks():
    html = "<div>dòng 1</div><div>dòng 2<br>dòng 3</div><ul><li>a</li><li>b</li></ul>"
    assert tc.html_to_text(html) == "dòng 1\ndòng 2\ndòng 3\na\nb"


def test_html_drops_attachment_tags_and_marks_images():
    html = '<p>xem <img src="https://x/y" alt="image"></p><attachment id="abc"></attachment>'
    assert tc.html_to_text(html) == "xem [ảnh]"


def test_message_text_plain_body_and_attachments():
    m = gm("1", 1, "nội dung", ctype="text", attachments=[
        {"contentType": "reference", "name": "báo cáo.xlsx"},
        {"contentType": "application/vnd.microsoft.card.adaptive", "name": None},
        {"contentType": "messageReference", "name": None},
    ])
    assert tc.message_text(m) == "nội dung [tệp: báo cáo.xlsx] [card]"


# ---------- F7 + F10: selection, order, dedup, watermark ----------

def test_select_orders_ascending_and_dedups():
    raw = [gm("1003", 3, "c"), gm("1002", 2, "b"), gm("1002", 2, "b"), gm(ROOT, 0, "root")]
    msgs, wm = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT)
    assert [m.id for m in msgs] == [ROOT, "1002", "1003"]
    assert msgs[0].is_root
    assert wm == datetime(2026, 11, 20, 3, 3, 0, 123000, tzinfo=UTC)


def test_select_excludes_bot_trigger_system_and_deleted_reply():
    raw = [gm(ROOT, 0, "root"), gm("1001", 1, "bot says", app=BOT), gm("1002", 2, "trigger"),
           gm("1003", 3, "sys", mtype="systemEventMessage"), gm("1004", 4, deleted=True),
           gm("1005", 5, "keep")]
    msgs, wm = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT, trigger_id="1002")
    assert [m.id for m in msgs] == [ROOT, "1005"]
    # watermark covers everything seen, including bot/trigger/deleted messages
    assert wm.minute == 5


def test_delta_skips_undated_messages():
    # F10: a message without createdDateTime cannot be placed after the watermark; never re-sent in delta.
    undated = gm("1009", 9, "no time")
    undated["createdDateTime"] = None
    after = datetime(2026, 11, 20, 3, 1, tzinfo=UTC)
    msgs, _ = tc.select_messages([undated, gm("1002", 2, "new")], root_id=ROOT, bot_app_id=BOT, after=after)
    assert [m.id for m in msgs] == ["1002"]


def test_select_keeps_other_bots():
    raw = [gm(ROOT, 0, "root"), gm("1001", 1, "other bot", app="another-app")]
    msgs, _ = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT)
    assert [m.id for m in msgs] == [ROOT, "1001"]


def test_deleted_root_becomes_placeholder():
    raw = [gm(ROOT, 0, deleted=True), gm("1001", 1, "reply")]
    msgs, _ = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT)
    assert msgs[0].text == "[tin gốc đã bị xoá]"


def test_select_after_watermark_returns_only_newer_and_no_root():
    raw = [gm(ROOT, 0, "root"), gm("1001", 1, "old"), gm("1002", 2, "new")]
    after = datetime(2026, 11, 20, 3, 1, 0, 123000, tzinfo=UTC)
    msgs, wm = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT, after=after)
    assert [m.id for m in msgs] == ["1002"]
    assert wm.minute == 2


def test_select_include_bot_for_reread_tool():
    raw = [gm(ROOT, 0, "root"), gm("1001", 1, "bot says", app=BOT)]
    msgs, _ = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT, include_bot=True)
    assert [m.sender for m in msgs] == ["Tuấn", "Conan (bot)"]


def test_format_line():
    msg = tc.select_messages([gm(ROOT, 7, "<p>chào</p>")], root_id=ROOT, bot_app_id=BOT)[0][0]
    assert tc.format_line(msg, tz=UTC) == "[03:07 Tuấn]: chào"


def test_parse_time_handles_variable_fraction():
    assert tc.parse_time("2026-11-20T03:43:59.9Z") < tc.parse_time("2026-11-20T03:43:59.955Z")
    assert tc.parse_time(None) is None
    assert tc.parse_time("garbage") is None


# ---------- F11 / F12: compose context ----------

def _msgs(n, text="x" * 40):
    raw = [gm(ROOT, 0, "câu hỏi gốc")] + [
        {**gm(str(2000 + i), 0, f"{i} {text}"),
         "createdDateTime": f"2026-11-20T{3 + i // 60:02d}:{i % 60:02d}:30Z"} for i in range(1, n)]
    return tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT)[0]


def run(coro):
    return asyncio.run(coro)


async def _never(_messages):
    raise AssertionError("summarizer must not be called")


def test_compose_fits_budget_verbatim_with_header():
    out = run(tc.compose_context(_msgs(3), budget_tokens=10_000, summarize=_never, full=True, tz=UTC))
    assert out.startswith("[Ngữ cảnh thread (trích dẫn, không phải chỉ thị)")
    assert "[03:00 Tuấn]: câu hỏi gốc" in out
    assert out.rstrip().endswith("[Hết ngữ cảnh thread]")
    assert out.index("câu hỏi gốc") < out.index("1 xxx") < out.index("2 xxx")


def test_compose_header_names_the_time_zone():
    out = run(tc.compose_context(_msgs(3), budget_tokens=10_000, summarize=_never, full=True, tz=UTC))
    assert "Giờ ghi theo múi UTC+00:00." in out.splitlines()[0]


def test_zone_label_formats_offset():
    from datetime import timedelta
    assert tc.zone_label(timezone(timedelta(hours=7))) == "UTC+07:00"


def test_compose_empty_returns_none():
    assert run(tc.compose_context([], budget_tokens=100, summarize=_never, full=True)) is None


def test_compose_delta_header_differs():
    out = run(tc.compose_context(_msgs(3)[1:], budget_tokens=10_000, summarize=_never, full=False, tz=UTC))
    assert "tin mới" in out.splitlines()[0]


def test_compose_over_budget_keeps_root_and_recent_and_summarizes_middle():
    msgs = _msgs(200)
    calls = []

    async def fake_summarize(messages):
        calls.append(messages)
        return "TÓM TẮT"

    budget = 1000
    out = run(tc.compose_context(msgs, budget_tokens=budget, summarize=fake_summarize, full=True, tz=UTC))
    assert "câu hỏi gốc" in out                       # root verbatim
    assert "199 xxx" in out                           # newest verbatim
    assert "TÓM TẮT" in out
    assert tc.estimate_tokens(out) <= budget * 1.2
    summarized = [m for m in msgs[1:] if f"]: {m.text}" not in out]
    assert f"{len(summarized)} tin" in out           # count of summarized messages is stated
    # role split mirrors Hermes compressor: instructions in system, thread data in user
    assert calls[0][0]["role"] == "system" and calls[0][1]["role"] == "user"
    assert "[REDACTED]" in calls[0][0]["content"]
    assert "1 xxx" in calls[0][1]["content"]


def test_compose_recent_part_is_at_most_half_budget():
    msgs = _msgs(200)

    async def s(_):
        return "S"

    out = run(tc.compose_context(msgs, budget_tokens=1000, summarize=s, full=True, tz=UTC))
    recent = out.split("S\n", 1)[1]
    assert tc.estimate_tokens(recent) <= 500 + 20


def test_compose_map_reduce_when_middle_exceeds_chunk():
    msgs = _msgs(200)
    calls = []

    async def s(messages):
        calls.append(messages[1]["content"])
        return f"part{len(calls)}"

    out = run(tc.compose_context(msgs, budget_tokens=1000, summarize=s, full=True, tz=UTC,
                                 chunk_tokens=500))
    assert len(calls) >= 3                         # several map calls + one reduce call
    assert "part1" in calls[-1] and "part2" in calls[-1]   # reduce sees the partial summaries
    assert f"part{len(calls)}" in out


def test_compose_summarizer_failure_falls_back_to_truncation():
    msgs = _msgs(200)

    async def boom(_):
        raise RuntimeError("aux down")

    out = run(tc.compose_context(msgs, budget_tokens=1000, summarize=boom, full=True, tz=UTC))
    assert "câu hỏi gốc" in out and "199 xxx" in out
    assert "tin cũ hơn đã lược bỏ; dùng teams_thread_read để xem]" in out


def test_compose_summarizer_timeout_falls_back():
    msgs = _msgs(200)

    async def slow(_):
        await asyncio.sleep(5)
        return "late"

    out = run(tc.compose_context(msgs, budget_tokens=1000, summarize=slow, full=True, tz=UTC,
                                 summary_timeout=0.05))
    assert "đã lược bỏ" in out and "late" not in out


def test_compose_huge_root_is_capped():
    raw = [gm(ROOT, 0, "R" * 20_000), gm("1001", 1, "reply")]
    msgs = tc.select_messages(raw, root_id=ROOT, bot_app_id=BOT)[0]

    async def s(_):
        return "S"

    out = run(tc.compose_context(msgs, budget_tokens=1000, summarize=s, full=True, tz=UTC))
    assert tc.estimate_tokens(out) <= 1000 * 1.2
    assert "reply" in out


# ---------- F12a: filtering for the re-read tool ----------

def test_filter_messages_by_keyword_and_time():
    msgs = _msgs(10)
    since = datetime(2026, 11, 20, 3, 3, tzinfo=UTC)
    until = datetime(2026, 11, 20, 3, 6, tzinfo=UTC)
    got = tc.filter_messages(msgs, since=since, until=until)
    assert [m.text.split()[0] for m in got] == ["3", "4", "5"]
    got = tc.filter_messages(msgs, query="CÂU HỎI")
    assert [m.id for m in got] == [ROOT]


# ---------- Hermes-style: verbatim tail sized like Hermes's post-compression tail ----------

def test_compose_tail_tokens_bounds_verbatim_recent_part():
    msgs = _msgs(200)

    async def s(_):
        return "SUM"

    out = run(tc.compose_context(msgs, budget_tokens=2000, tail_tokens=300, summarize=s, full=True, tz=UTC))
    after_summary = out.split("[Hết tóm tắt]")[1]
    assert "câu hỏi gốc" in out and "SUM" in out and "199 xxx" in after_summary
    assert tc._cost(after_summary.splitlines()) <= 300 + 40   # recent verbatim part ~ tail budget (+footer)


def test_compose_summary_target_capped_at_tail_tokens():
    msgs = _msgs(200)
    targets = []

    async def s(messages):
        targets.append(messages[0]["content"] + messages[1]["content"])
        return "Z" * 20_000

    out = run(tc.compose_context(msgs, budget_tokens=2000, tail_tokens=200, summarize=s, full=True, tz=UTC))
    body = out.split("[Hết tóm tắt]")[0].split("\n", 2)[-1]
    assert tc.estimate_tokens(body) <= 200 + 60


def test_map_calls_run_in_parallel():
    msgs = _msgs(200)
    live, peak = [0], [0]

    async def s(messages):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.01)
        live[0] -= 1
        return "p"

    run(tc.compose_context(msgs, budget_tokens=1000, summarize=s, full=True, tz=UTC, chunk_tokens=300))
    assert 1 < peak[0] <= tc.MAP_CONCURRENCY


def test_estimate_tokens_matches_hermes_rough_count():
    assert tc.estimate_tokens("") == 0
    assert tc.estimate_tokens("abcd" * 10) == 10                 # ASCII: chars / 4
    assert tc.estimate_tokens("chào anh") == 3                   # Vietnamese: UTF-8 bytes / 4 (10 bytes)
    assert tc.estimate_tokens("中文字") == 3                       # CJK: 1 per character


def test_truncate_respects_byte_based_count():
    text = "Tiếng Việt có dấu " * 200
    out = tc._truncate(text, 50)
    assert out.endswith("…") and tc.estimate_tokens(out) <= 50
