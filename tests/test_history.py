from mxtop.ui.history import HistoryGraph, HostHistory


def test_history_graph_renders_blank_when_empty():
    graph = HistoryGraph(5)

    lines = graph.render(10)

    assert len(lines) == 5
    assert all(line == " " * 10 for line in lines)


def test_history_graph_packs_two_samples_per_column():
    graph = HistoryGraph(1)
    graph.add(100)
    graph.add(100)

    lines = graph.render(3)

    assert lines == ["  ⣿"]


def test_history_graph_keeps_tick_for_zero_samples():
    graph = HistoryGraph(5)
    graph.add(0)
    graph.add(0)

    lines = graph.render(2)

    assert lines[-1][-1] != " ", "zero samples should still draw a visible tick"
    assert all(char == " " for char in lines[0])


def test_history_graph_upsidedown_hangs_from_top():
    up = HistoryGraph(4)
    down = HistoryGraph(4, upsidedown=True)
    for graph in (up, down):
        graph.add(50)
        graph.add(50)

    up_lines = up.render(1)
    down_lines = down.render(1)

    assert up_lines[0] == " " and up_lines[-1] != " "
    assert down_lines[0] != " " and down_lines[-1] == " "


def test_history_graph_full_column_uses_full_braille_cell():
    graph = HistoryGraph(2)
    graph.add(100)
    graph.add(100)

    lines = graph.render(1)

    assert lines == ["⣿", "⣿"]


def test_history_graph_ignores_none_samples():
    graph = HistoryGraph(3)
    graph.add(None)
    graph.add(None)

    assert graph.render(1) == [" ", " ", " "]


def test_host_history_buckets_by_interval():
    history = HostHistory(interval=1.0)
    history.sample(cpu=10, now=0.0)
    history.sample(cpu=30, now=0.5)

    assert len(history.cpu.samples) == 0, "samples inside one interval stay buffered"

    history.sample(cpu=50, now=1.5)

    assert len(history.cpu.samples) == 1
    assert history.cpu.last_value == 30.0, "flushed value is the bucket average"


def test_host_history_records_missing_metrics_as_gaps():
    history = HostHistory(interval=1.0)
    history.sample(cpu=10, now=0.0)
    history.sample(cpu=20, gpu_memory=None, now=1.5)

    assert history.gpu_memory.samples[-1] is None


def test_host_history_reset_clears_graphs():
    history = HostHistory(interval=1.0)
    history.sample(cpu=10, now=0.0)
    history.sample(cpu=20, now=1.5)
    history.reset()

    assert len(history.cpu.samples) == 0
    history.sample(cpu=10, now=10.0)
    history.sample(cpu=10, now=11.5)
    assert len(history.cpu.samples) == 1


def test_history_hold_suppresses_sampling():
    from mxtop.ui.history import HostHistory

    history = HostHistory(interval=1.0)
    history.sample(cpu=10.0, now=0.0)
    history.sample(cpu=20.0, now=1.5)
    assert list(history.cpu.samples)

    before = list(history.cpu.samples)
    history.hold = True
    history.sample(cpu=99.0, now=3.0)
    history.sample(cpu=99.0, now=4.5)
    assert list(history.cpu.samples) == before

    history.hold = False
    history.sample(cpu=30.0, now=6.0)
    history.sample(cpu=30.0, now=7.5)
    assert list(history.cpu.samples) != before
