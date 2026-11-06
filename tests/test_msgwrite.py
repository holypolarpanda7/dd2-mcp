"""Round trip every game .msg through msgwrite (byte for byte), and an added entry reads back through msg.py."""
import sys

from dd2mcp import msg, msgwrite, pak


def roundtrip(limit=None):
    files = msg.files()[:limit] if limit else msg.files()
    ok, bad, skipped = 0, [], 0
    for f in files:
        data = pak.find(f)[1]
        try:
            m = msgwrite.MsgFile(data)
        except ValueError as e:
            skipped += 1
            continue
        out = m.to_bytes()
        if out == bytes(data):
            ok += 1
        else:
            first = next((i for i, (a, b) in enumerate(zip(out, data)) if a != b), min(len(out), len(data)))
            bad.append((f, len(data), len(out), hex(first)))
    return ok, bad, skipped


def test_add():
    f = "natives/stm/message/ui/job03act.msg.24"
    m = msgwrite.MsgFile(pak.find(f)[1])
    e = m.add("wizard_test_00", {"English": "Detect Magic", "Japanese": "ディテクト・マジック"})
    back = msg.Msg(m.to_bytes())
    hit = next(x for x in back.entries if x.guid == e.guid)
    assert hit.text("English") == "Detect Magic" and hit.text("Japanese") == "ディテクト・マジック" and hit.text("French") == "Detect Magic"
    old = msg.Msg(pak.find(f)[1])
    assert len(back.entries) == len(old.entries) + 1
    assert all(a.texts == b.texts and a.guid == b.guid for a, b in zip(old.entries, back.entries))
    return "add ok"


if __name__ == "__main__":
    ok, bad, skipped = roundtrip(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"round trip: {ok} identical, {len(bad)} different, {skipped} skipped (other versions)")
    for b in bad[:10]:
        print("  DIFF", *b)
    print(test_add())
