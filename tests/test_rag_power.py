import os
import time

from rea.evaluation.power import parse_line, summarize_power
from rea.rag import INDEX_NAME, Bm25Index, Passage, build_index, load_or_build, query_for, tokenize


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("The relays are tripping on negative-sequence currents") == [
        "relay", "tripping", "negative", "sequence", "current"]


def test_bm25_ranks_the_relevant_passage_first():
    idx = Bm25Index([
        Passage("a.txt", 1, "Breaker failure protection retrips and then trips adjacent breakers."),
        Passage("b.txt", 1, "The negative-sequence directional element compares z2 with the forward threshold."),
        Passage("c.txt", 1, "Inverse-time overcurrent curves use a time dial and pickup."),
    ])
    hits = idx.search("forward fault negative-sequence directional element", k=2)
    assert hits[0].source == "b.txt" and hits[0].tag == "P1"


def test_index_is_cached_and_rebuilt_when_papers_change(tmp_path):
    (tmp_path / "README.md").write_text("not indexed")
    (tmp_path / "dir.txt").write_text("Directional elements decide forward or reverse using negative sequence. " * 5)
    idx = load_or_build(tmp_path)
    assert idx is not None and {p.source for p in idx.passages} == {"dir.txt"}
    assert (tmp_path / INDEX_NAME).exists()
    assert load_or_build(tmp_path).files == idx.files  # served from the cache
    time.sleep(0.01)
    (tmp_path / "toc.txt").write_text("Time-overcurrent elements integrate toward a trip using the curve. " * 5)
    os.utime(tmp_path / "toc.txt")
    assert {p.source for p in load_or_build(tmp_path).passages} == {"dir.txt", "toc.txt"}


def test_no_papers_means_no_retrieval(tmp_path):
    assert load_or_build(tmp_path) is None
    assert load_or_build(tmp_path / "missing") is None


def test_query_uses_core_findings(bg_sheet):
    q = query_for(bg_sheet)
    assert "B-phase-to-ground fault" in q and "forward" in q and "inverse-time overcurrent" in q


def test_build_index_reads_text_files(tmp_path):
    (tmp_path / "p.md").write_text("word " * 400)
    idx = build_index(tmp_path)
    assert len(idx.passages) >= 3


# ---------------------------------------------------------------- tegrastats

ORIN = ("10-09-2026 14:01:02 RAM 3012/7620MB (lfb 2x4MB) SWAP 0/3810MB CPU [12%@1510,8%@1510] "
        "GR3D_FREQ 61% cpu@48.5C soc2@47.1C VDD_IN 7123mW/6950mW VDD_CPU_GPU_CV 2101mW/1990mW "
        "VDD_SOC 1702mW/1688mW")
OLD = "RAM 1922/3964MB CPU [5%@1479] GR3D_FREQ 0% POM_5V_IN 3816/3816 POM_5V_GPU 0/0 POM_5V_CPU 816/816"


def test_parse_tegrastats_lines():
    assert parse_line(ORIN) == {"VDD_IN": 7.123, "VDD_CPU_GPU_CV": 2.101, "VDD_SOC": 1.702}
    assert parse_line(OLD)["POM_5V_IN"] == 3.816
    assert parse_line("RAM 3012/7620MB") == {}  # memory is not a power rail


def test_summarize_power(tmp_path):
    log = tmp_path / "t.log"
    log.write_text("\n".join([ORIN, ORIN.replace("VDD_IN 7123mW", "VDD_IN 9123mW")]) + "\n")
    s = summarize_power(log, n_events=4, interval_ms=500)
    assert s["rail"] == "VDD_IN" and s["samples"] == 2
    assert s["mean_w"] == 8.123 and s["max_w"] == 9.123
    assert s["energy_j"] == round(8.123 * 1.0, 1) and s["joules_per_event"] == round(8.123 / 4, 2)


def test_download_lists_are_not_indexed(tmp_path):
    (tmp_path / "sel-papers.txt").write_text("https://example.invalid/a.pdf  # a list, not a paper\n" * 20)
    (tmp_path / "notes.txt").write_text("Directional elements use negative-sequence quantities. " * 10)
    idx = load_or_build(tmp_path)
    assert {p.source for p in idx.passages} == {"notes.txt"}


def test_network_state_reads_links_without_sending_anything(monkeypatch, tmp_path):
    import rea.cli as cli

    net = tmp_path / "net"
    for iface, carrier in (("lo", "1"), ("eth0", "0")):
        (net / iface).mkdir(parents=True)
        (net / iface / "carrier").write_text(carrier)
    real_path = cli.Path

    def fake_path(p, *rest):
        if str(p) == "/sys/class/net":
            return real_path(net)
        if str(p) == "/proc/net/route":
            return real_path(tmp_path / "route")
        return real_path(p, *rest)

    (tmp_path / "route").write_text("Iface\tDestination\tGateway\neth0\t00000000\t0101A8C0\n")
    monkeypatch.setattr(cli, "Path", fake_path)
    assert cli.network_state() == "OFFLINE: no network interface has a link (a stale default route via eth0 remains)"
    (net / "eth0" / "carrier").write_text("1")
    assert cli.network_state().startswith("link up on eth0; default route via eth0")


def test_prebuilt_index_is_used_from_a_read_only_folder(tmp_path, monkeypatch):
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "dir.txt").write_text("Directional elements decide forward or reverse. " * 20)
    built = build_index(papers)  # written next to the papers
    monkeypatch.setenv("REA_INDEX_DIR", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    again = load_or_build(papers)
    assert again.files == built.files
    assert not (tmp_path / "scratch" / INDEX_NAME).exists()  # nothing rebuilt
