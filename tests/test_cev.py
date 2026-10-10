"""SEL compressed event (.cev) reader and COMTRADE conversion, on a synthetic file."""

import math

import pytest

from rea.cev import CevError, checksum_ok, convert, read_cev


def _line(*fields: str) -> str:
    body = ",".join(fields) + ","
    return body + '"%04X"' % (sum(body.encode("latin-1")) % 65536)


def make_cev(rows: int = 160, spc: int = 16, digital_every: int = 1, trigger_row: int = 48) -> str:
    lines = [
        _line('"FID"'),
        _line('"FID=SEL-351-7-R000-V0-Z000000-D20200101"'),
        _line('"MONTH"', '"DAY"', '"YEAR"', '"HOUR"', '"MIN"', '"SEC"', '"MSEC"'),
        _line("3", "4", "2021", "5", "6", "7", "250"),
        _line('"FREQ"', '"SAM/CYC_A"', '"SAM/CYC_D"', '"NUM_OF_CYC"', '"EVENT"', '"TARGETS"', '"IA"'),
        _line("60.00", str(spc), str(spc // digital_every), str(rows // spc), '"AG T"', '"INST"', "1200"),
        _line('"IA"', '"IB"', '"IC"', '"VA(kV)"', '"VB(kV)"', '"VC(kV)"', '"TRIG"', '"TRIP 52A OUT101 *"'),
    ]
    for k in range(rows):
        w = 2 * math.pi * k / spc
        ia = 1000 * math.cos(w) if k >= trigger_row else 100 * math.cos(w)
        vals = [ia, 100 * math.cos(w - 2.094), 100 * math.cos(w + 2.094),
                66.4 * math.cos(w), 66.4 * math.cos(w - 2.094), 66.4 * math.cos(w + 2.094)]
        f = [f"{v:.1f}" for v in vals]
        trig = '">"' if k == trigger_row else " "
        if k % digital_every == 0:
            trip = 1 if k >= trigger_row + 8 else 0
            bits = f"{trip}{1 if k < trigger_row + 12 else 0}{trip}0"  # TRIP, 52A, OUT101, *
            dig = '"%X"' % int(bits, 2)
        else:
            dig = " "
        lines.append(_line(*f, trig, dig))
    lines.append(_line('"SETTINGS"'))
    lines.append("CTR   = 240      PTR   = 1000.00  PHROT = ABC")
    lines.append("Z1MAG = 7.80     Z1ANG = 84.00    E32   = AUTO")
    lines.append('","1234"')
    return "\r\n".join(lines) + "\r\n"


@pytest.fixture
def cev_file(tmp_path):
    p = tmp_path / "event.cev"
    p.write_text(make_cev(), encoding="latin-1")
    return p


def test_checksums():
    assert checksum_ok(_line('"FID"'))
    assert not checksum_ok('"FID","0000"')


def test_read(cev_file):
    ev = read_cev(cev_file)
    assert ev.relay == "SEL-351-7"
    assert ev.bad_checksums == 0
    assert ev.trigger_index == 48 and ev.samples_per_cycle == 16 and ev.nominal_hz == 60.0
    assert ev.summary["EVENT"] == "AG T"
    assert ev.analog_labels == ["IA", "IB", "IC", "VA(kV)", "VB(kV)", "VC(kV)"]
    assert ev.digital_names == ["TRIP", "52A", "OUT101", "*"]
    trip = ev.digital[0]
    assert trip.index(1) == 56 and ev.digital[1].index(0) == 60
    assert ev.settings["CTR"] == "240" and ev.settings["PHROT"] == "ABC" and ev.settings["E32"] == "AUTO"
    assert ev.trigger_time.microsecond == 250000


def test_low_rate_digitals_cover_the_rows_before_them(tmp_path):
    p = tmp_path / "slow.cev"
    p.write_text(make_cev(digital_every=4), encoding="latin-1")
    ev = read_cev(p)
    # TRIP is recorded at row 56 (a digital row); rows 53-55 belong to that sample.
    assert ev.digital[0].index(1) == 53


def test_corrupt_line_is_counted(tmp_path):
    lines = make_cev().split("\r\n")
    body, cs = lines[10].rsplit(",", 1)
    lines[10] = body.replace("1", "2", 1) + "," + cs  # change a digit, keep the old checksum
    p = tmp_path / "bad.cev"
    p.write_text("\r\n".join(lines), encoding="latin-1")
    assert read_cev(p).bad_checksums == 1


def test_not_a_cev(tmp_path):
    p = tmp_path / "x.cev"
    p.write_text("hello\n" * 20)
    with pytest.raises(CevError):
        read_cev(p)


def test_convert_writes_comtrade_with_ratios(cev_file, tmp_path):
    cfg, settings, ev = convert(cev_file, tmp_path / "out")
    lines = cfg.read_text(encoding="latin-1").splitlines()
    assert lines[0].endswith(",1999") and lines[1] == "9,6A,3D"  # the '*' placeholder is dropped
    ia = lines[2].split(",")
    assert ia[1] == "IA" and ia[2] == "A" and ia[4] == "A" and ia[10:13] == ["240", "1", "P"]
    va = lines[5].split(",")
    assert va[4] == "kV" and va[10:13] == ["1000", "1", "P"]
    assert lines[-4] == "04/03/2021,05:06:07.200000"  # trigger minus 48 samples at 960 Hz
    assert lines[-3] == "04/03/2021,05:06:07.250000"
    s = settings.read_text()
    assert "Z1MAG := 7.80" in s and "PHROT := ABC" in s and "E32 = AUTO" in s
    # the first data row reproduces the first sample within the quantization step
    row = (tmp_path / "out" / "event.dat").read_text().splitlines()[0].split(",")
    a, b = float(ia[5]), float(ia[6])
    assert abs(a * int(row[2]) + b - ev.analog[0][0]) <= a


def test_core_reads_the_conversion(core_bin, cev_file, tmp_path):
    from rea.core_runner import run_core
    from rea.facts import FactSheet

    cfg, settings, _ = convert(cev_file, tmp_path / "out")
    sheet = FactSheet(run_core(cfg, settings, core=core_bin)[0])
    assert sheet.value("record.sample_rate_hz") == 960.0
    assert sheet.value("settings.CTR") == 240.0
    assert sheet.value("fault.detected") is True
    assert sheet.value("fault.type") == "AG"
    assert abs(sheet.value("fault.inception_from_trigger_ms")) < 1.1  # within one sample of the trigger row


def test_analyze_a_cev_directly(core_bin, cev_file):
    from rea.pipeline import analyze_event

    r = analyze_event(cev_file, backend="template", papers=None, core=str(core_bin))
    assert r.cfg.name == "event.cev" and r.sheet.value("fault.type") == "AG"
    assert r.narrative.validation.rejected == []


def test_rotation_check_from_the_relay_summary():
    from rea.evaluation.real_events import summary_rotation_check

    # The SEL-311L webinar event: balanced phase currents, 3I2 three times as large.
    note = summary_rotation_check({"IA": "204", "IB": "204", "IC": "204", "IG": "5", "3I2": "614"})
    assert note is not None and "3.01 times" in note
    # A real unbalance (a fault) or a small 3I2 says nothing about rotation.
    assert summary_rotation_check({"IA": "489", "IB": "159", "IC": "437", "3I2": "590"}) is None
    assert summary_rotation_check({"IA": "204", "IB": "204", "IC": "204", "3I2": "4"}) is None
    assert summary_rotation_check({"IA(A)": "1382.6"}) is None
