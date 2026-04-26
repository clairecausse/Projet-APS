import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DATA_DIR = os.path.join("data_nappes", "data_nappes", "Data")
OUVRAGES_PATH = os.path.join("data_nappes", "data_nappes", "OUVRAGES.csv")

VARIABLES = ["GWL", "P", "T", "ET", "NDVI"]
LABELS = {
    "GWL": "Niveau nappe (m)",
    "P":   "Précipitations (mm/mois)",
    "T":   "Température (°C)",
    "ET":  "Évapotranspiration (mm/mois)",
    "NDVI": "NDVI (sans unité)",
}
COLORS = ["steelblue", "seagreen", "tomato", "goldenrod", "mediumpurple"]


def load_well(well_id):
    path = os.path.join(DATA_DIR, f"{well_id}.csv")
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def normalize(series):
    mean, std = series.mean(), series.std()
    if std == 0:
        return series - mean
    return (series - mean) / std


def plot_well(well_id, save_dir=None):
    df = load_well(well_id)

    fig_raw, axes_raw = plt.subplots(len(VARIABLES), 1, figsize=(12, 10), sharex=True)
    fig_norm, axes_norm = plt.subplots(len(VARIABLES), 1, figsize=(12, 10), sharex=True)

    fig_raw.suptitle(f"Puit {well_id} — Valeurs brutes", fontsize=14, fontweight="bold")
    fig_norm.suptitle(f"Puit {well_id} — Valeurs normalisées (z-score)", fontsize=14, fontweight="bold")

    for i, (var, color) in enumerate(zip(VARIABLES, COLORS)):
        series = df[var].dropna()
        dates = df.loc[series.index, "date"]

        # Figure A : brut
        axes_raw[i].plot(dates, series, color=color, linewidth=1.2)
        axes_raw[i].set_ylabel(LABELS[var], fontsize=8)
        axes_raw[i].grid(True, alpha=0.3)

        # Figure B : normalisé
        axes_norm[i].plot(dates, normalize(series), color=color, linewidth=1.2)
        axes_norm[i].set_ylabel(LABELS[var], fontsize=8)
        axes_norm[i].axhline(0, color="black", linewidth=0.5, linestyle="--")
        axes_norm[i].grid(True, alpha=0.3)

    for fig, axes in [(fig_raw, axes_raw), (fig_norm, axes_norm)]:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        axes[-1].xaxis.set_major_locator(mdates.YearLocator(2))
        fig.autofmt_xdate(rotation=45)
        fig.tight_layout()

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        fig_raw.savefig(os.path.join(save_dir, f"{well_id}_brut.png"), dpi=100)
        fig_norm.savefig(os.path.join(save_dir, f"{well_id}_normalise.png"), dpi=100)
        plt.close(fig_raw)
        plt.close(fig_norm)
        print(f"  Saved: {well_id}")
    else:
        plt.show()


def plot_sample(n_per_region=1, save_dir="graphiques"):
    ouvrages = pd.read_csv(OUVRAGES_PATH)
    ouvrages["Ouvrage"] = ouvrages["Ouvrage"].astype(str)

    available = set(f.replace(".csv", "") for f in os.listdir(DATA_DIR))
    ouvrages = ouvrages[ouvrages["Ouvrage"].isin(available)]

    frames = []
    for _, group in ouvrages.groupby("Region"):
        frames.append(group.sample(min(n_per_region, len(group)), random_state=42))
    sample = pd.concat(frames).reset_index(drop=True)

    print(f"Tracé de {len(sample)} puits ({n_per_region} par région) :")
    for _, row in sample.iterrows():
        print(f"  Puit {row['Ouvrage']} — {row['Region']}")
        plot_well(row["Ouvrage"], save_dir=save_dir)

    print(f"\nGraphiques sauvegardés dans : {save_dir}/")


if __name__ == "__main__":
    # Trace 1 puit par région et sauvegarde les graphiques dans graphiques/
    plot_sample(n_per_region=1, save_dir="graphiques")
