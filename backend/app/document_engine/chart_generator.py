from __future__ import annotations
import io
import base64
import os
from typing import Optional, Union
from .ast_types import ChartDirective


def _normalize_data(data: Union[list, dict, None]) -> list[dict]:
    if isinstance(data, dict):
        return [{"label": k, "value": v} for k, v in data.items()]
    elif isinstance(data, list):
        return data
    return []


def generate_chart_png(chart: ChartDirective, output_dir: str) -> Optional[str]:
    filename = f"chart_{id(chart)}_{chart.chart_type}.png"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    plt.close("all")
    fig, ax = plt.subplots(figsize=(6, 4))

    data = _normalize_data(chart.data)
    labels = [d.get("label", str(i)) for i, d in enumerate(data)]
    values = [float(d.get("value", 0)) for d in data]

    colors = ["#4A90D9", "#50C878", "#E8833A", "#D94A4A", "#9B59B6", "#F1C40F", "#1ABC9C", "#E67E22"]

    ct = chart.chart_type.lower()
    if ct == "bar":
        bars = ax.bar(labels, values, color=colors[:len(labels)])
        ax.set_ylabel("Values")
    elif ct == "line":
        ax.plot(labels, values, marker="o", color=colors[0], linewidth=2)
        ax.set_ylabel("Values")
    elif ct == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", colors=colors[:len(labels)])
    elif ct == "scatter":
        ax.scatter(range(len(values)), values, c=colors[0], s=60)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        ax.set_ylabel("Values")
    elif ct == "area":
        ax.fill_between(range(len(values)), values, alpha=0.3, color=colors[0])
        ax.plot(range(len(values)), values, color=colors[0], linewidth=2)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        ax.set_ylabel("Values")
    elif ct == "horizontal_bar":
        bars = ax.barh(labels, values, color=colors[:len(labels)])
        ax.set_xlabel("Values")
    else:
        bars = ax.bar(labels, values, color=colors[:len(labels)])

    if chart.title:
        ax.set_title(chart.title, fontsize=14, pad=12)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return filepath


def generate_chart_svg(chart: ChartDirective) -> Optional[str]:
    try:
        filepath = generate_chart_png(chart, "/tmp/llmdash_charts")
        if not filepath:
            return None
        with open(filepath, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("ascii")
        width = _chart_width(chart)
        return f'<img src="data:image/png;base64,{img_data}" style="width:{width}px;max-width:100%;height:auto;" alt="{chart.title}" />'
    except Exception:
        return None


def generate_chart_bytes(chart: ChartDirective) -> Optional[bytes]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    plt.close("all")
    fig, ax = plt.subplots(figsize=(6, 4))

    data = _normalize_data(chart.data)
    labels = [d.get("label", str(i)) for i, d in enumerate(data)]
    values = [float(d.get("value", 0)) for d in data]
    colors = ["#4A90D9", "#50C878", "#E8833A", "#D94A4A", "#9B59B6", "#F1C40F", "#1ABC9C", "#E67E22"]

    ct = chart.chart_type.lower()
    if ct == "bar":
        ax.bar(labels, values, color=colors[:len(labels)])
    elif ct == "line":
        ax.plot(labels, values, marker="o", color=colors[0], linewidth=2)
    elif ct == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", colors=colors[:len(labels)])
    elif ct == "scatter":
        ax.scatter(range(len(values)), values, c=colors[0], s=60)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
    elif ct == "area":
        ax.fill_between(range(len(values)), values, alpha=0.3, color=colors[0])
        ax.plot(range(len(values)), values, color=colors[0], linewidth=2)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
    elif ct == "horizontal_bar":
        ax.barh(labels, values, color=colors[:len(labels)])
    else:
        ax.bar(labels, values, color=colors[:len(labels)])

    if chart.title:
        ax.set_title(chart.title, fontsize=14, pad=12)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _chart_width(chart: ChartDirective) -> int:
    try:
        w = int(chart.options.get("width", 600))
        return min(max(w, 200), 1200)
    except (ValueError, TypeError):
        return 600
