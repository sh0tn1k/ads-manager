"""Run the policy backtest and write docs/agent-sim-report.md (+ PNG plots in docs/sim/).

All environment values are explicit CLI arguments. Defaults are the plan's section 4 starting
observations used as a *fixture* (CTR 1.25%, CVR 6.7%, CPM 0.13 TON); the hurdle default is a test
value, not the owner's HURDLE_CPA_TON.

    python scripts/run_sim.py --reps 200
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sim import SimConfig, run_experiment  # noqa: E402


def fmt(x: float) -> str:
    return "∞" if not math.isfinite(x) else f"{x:.3g}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--samples", type=int, default=1000)
    ap.add_argument("--budgets", default="2,5,10,20,40,80,160")
    ap.add_argument("--base-ctr", type=float, default=0.0125)
    ap.add_argument("--base-cvr", type=float, default=0.067)
    ap.add_argument("--base-cpm", type=float, default=0.13)
    ap.add_argument("--hurdle", type=float, default=0.15)
    ap.add_argument("--temper", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "docs" / "agent-sim-report.md"))
    args = ap.parse_args(argv)

    cfg = SimConfig(base_ctr=args.base_ctr, base_cvr=args.base_cvr, base_cpm=args.base_cpm, hurdle=args.hurdle)
    model_kwargs = dict(
        prior_ctr=args.base_ctr, prior_ctr_strength=1000.0, prior_cvr=args.base_cvr, prior_cvr_strength=50.0,
        temper=args.temper,
    )
    budgets = [float(b) for b in args.budgets.split(",")]
    started = time.time()
    rep = run_experiment(cfg, budgets=budgets, reps=args.reps, model_kwargs=model_kwargs, samples=args.samples, seed=args.seed)
    elapsed = time.time() - started

    out = Path(args.out)
    plots = out.parent / "sim"
    plots.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for metric, title, fname in (
            (rep.mean_regret, "Среднее относительное сожаление", "regret.png"),
            (rep.p_within20, "P(выбрана ячейка в пределах 20% от лучшей)", "within20.png"),
        ):
            fig, ax = plt.subplots(figsize=(6, 4))
            for name, values in metric.items():
                ax.plot(budgets, values, marker="o", label=name)
            ax.set_xscale("log")
            ax.set_xlabel("Бюджет, TON")
            ax.set_title(title)
            ax.grid(True, alpha=0.3)
            ax.legend()
            fig.tight_layout()
            fig.savefig(plots / fname, dpi=110)
            plt.close(fig)
        plotted = True
    except ImportError:
        plotted = False

    savings = rep.savings_vs_grid("proposed")
    reached = [s for s in savings if s[2] >= 2.0]
    lines = [
        "# Симулятор и бэктест политик (фаза 3)",
        "",
        f"Сгенерировано `scripts/run_sim.py` (seed={args.seed}, повторов={args.reps}, MC-сэмплов={args.samples}, "
        f"время {elapsed:.0f} с). Числа ниже — результат прогона, ничего не подгонялось.",
        "",
        "## Среда",
        "",
        f"- {cfg.n_geo} гео × {cfg.n_angle} углов креатива = {cfg.n_geo * cfg.n_angle} ячеек.",
        f"- Центры (фикстура из стартовых наблюдений раздела 4): CTR {cfg.base_ctr:.2%}, CVR {cfg.base_cvr:.1%}, "
        f"CPM {cfg.base_cpm} TON. Порог для P(CPA<c*) в симуляции — {cfg.hurdle} TON (тестовое значение).",
        f"- Разброс эффектов (σ логитов): CTR гео {cfg.sd_geo_ctr}, угол {cfg.sd_angle_ctr}; CVR гео {cfg.sd_geo_cvr}, "
        f"угол {cfg.sd_angle_cvr}; взаимодействие {cfg.sd_interaction}; CPM гео lognormal σ={cfg.sd_cpm_geo}.",
        "- Шум: биномиальные клики и вступления. Нестационарность (матчдни, аукцион) и ограничение объёма не моделируются.",
        "",
        "## Политики",
        "",
        "- `random` — случайные ячейки по 8 TON (бюджет стадии 3), лучшая по наблюдаемому CPA;",
        "- `grid` — полная сетка, бюджет поровну;",
        "- `halving` — successive halving без модели (η=3, первый раунд по CPC, далее по CPA);",
        "- `proposed` — модель (PosteriorModel v1 + CpmModel) + стадии: холодный старт (покрытие всех уровней), "
        "скрининг ceil(K/η)=8 ячеек по P(CPA<c*), валидация ceil(8/η)=3 лидеров; доли бюджета 30/35/35%;",
        "- `proposed_4_2` — первый опробованный вариант той же политики: 4 ячейки на скрининге, 2 на валидации. "
        "Оставлен в отчёте, чтобы было видно, что пробовалось.",
        "",
        "## Среднее относительное сожаление CPA(выбранной)/CPA(лучшей) − 1",
        "",
        "| Политика | " + " | ".join(f"{fmt(b)} TON" for b in budgets) + " |",
        "|---|" + "---|" * len(budgets),
    ]
    for name, values in rep.mean_regret.items():
        lines.append(f"| {name} | " + " | ".join(f"{v:.3f}" for v in values) + " |")
    lines += ["", "## P(выбранная ячейка в пределах 20% от лучшей)", "",
              "| Политика | " + " | ".join(f"{fmt(b)} TON" for b in budgets) + " |", "|---|" + "---|" * len(budgets)]
    for name, values in rep.p_within20.items():
        lines.append(f"| {name} | " + " | ".join(f"{v:.2f}" for v in values) + " |")
    lines += ["", "## P(выбрана одна из трёх лучших)", "",
              "| Политика | " + " | ".join(f"{fmt(b)} TON" for b in budgets) + " |", "|---|" + "---|" * len(budgets)]
    for name, values in rep.p_top3.items():
        lines.append(f"| {name} | " + " | ".join(f"{v:.2f}" for v in values) + " |")
    lines += ["", "## Расход до выбора ячейки в пределах 20% от лучшей", "",
              "Медиана по повторам минимального бюджета из сетки, при котором выбор попал в 20%; "
              "в скобках доля повторов, где это не случилось ни при каком бюджете.", ""]
    for name in rep.mean_regret:
        lines.append(f"- {name}: {fmt(rep.spend_to_within20[name])} TON ({rep.never_within20[name]:.0%})")
    lines += ["", "## Целевой показатель: то же сожаление минимум вдвое дешевле полной сетки", "",
              "| Бюджет сетки | Сожаление сетки | Бюджет `proposed` для того же сожаления | Экономия |", "|---|---|---|---|"]
    for (b, need, ratio), r in zip(savings, rep.mean_regret["grid"]):
        lines.append(f"| {fmt(b)} | {r:.3f} | {fmt(need)} | {ratio:.2f}× |")
    verdict = (
        "**Цель достигнута** при бюджетах сетки: " + ", ".join(fmt(s[0]) for s in reached) + " TON; "
        "при остальных — нет."
        if reached
        else "**Цель не достигнута** ни при одном бюджете сетки."
    )
    lines += ["", verdict, "",
              "## Калибровка (только `proposed`, протестированные ячейки, финальная модель)", ""]
    for k, v in rep.coverage.items():
        lines.append(f"- доля истинных {k.upper()} внутри 80%-интервала (p10–p90): {v:.1%} (цель ≈ 80%)")
    lines += ["", "## Выводы и ограничения", "",
              "- Сравнение честное: у всех политик одни и те же среды (common random numbers по seed).",
              "- Экономия считается только внутри сетки бюджетов: если `proposed` уже на минимальном бюджете лучше сетки, "
              "отношение ограничено снизу (строка «1.00×» при 2 TON означает «не меньше 1×», экстраполяции нет).",
              "- Successive halving без модели (`halving`) на этой среде не хуже, а на средних и больших бюджетах лучше `proposed`. "
              "Модель пока окупается только на малых бюджетах, где важен перенос информации между ячейками.",
              "- `proposed` упирается в остаточное сожаление: после холодного старта дальше идут только ячейки, "
              "выбранные аддитивной моделью, а она не видит взаимодействий гео×угол и ошибается при малых данных. "
              "На больших бюджетах сетка и halving лучше.",
              "- Калибровка: CPA-интервалы шире номинальных 80% (консервативно, из-за temper 0.6 и широких приоров), CTR-интервалы близки к номиналу. "
              "Калибровать temper и σ после реальных данных.",
              "Это консервативно, но снижает остроту ранжирования; калибровать temper и σ после реальных данных.",
              "- Числа зависят от фикстуры среды (разброс эффектов). На реальной истории их надо перепроверить.",
              ""]
    if plotted:
        lines += ["![regret](sim/regret.png)", "", "![within20](sim/within20.png)", ""]
    lines += ["<details><summary>Сырые данные</summary>", "", "```json",
              json.dumps({"budgets": budgets, "mean_regret": rep.mean_regret, "p_within20": rep.p_within20,
                          "p_top3": rep.p_top3, "coverage": rep.coverage}, indent=1), "```", "</details>", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
