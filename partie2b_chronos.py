import os
import numpy as np
import pandas as pd
import torch
from chronos import BaseChronosPipeline
from sklearn.metrics import mean_squared_error, mean_absolute_error

# ─── Chemins ────────────────────────────────────────────────────────────────
DATA_DIR      = os.path.join("data_nappes", "data_nappes", "Data")
OUVRAGES_PATH = os.path.join("data_nappes", "data_nappes", "OUVRAGES.csv")

# ─── Paramètres (fixés par les consignes) ───────────────────────────────────
N_TEST       = 12    # mois réservés pour le test
MIN_CONTEXT  = 24    # contexte minimum requis (mois non-NaN)
BATCH_SIZE   = 32    # puits traités en parallèle par Chronos
MODEL_NAME   = "amazon/chronos-bolt-small"


# ═══════════════════════════════════════════════════════════════════════════
# 1. CHARGEMENT
# ═══════════════════════════════════════════════════════════════════════════

def load_well_gwl(well_id):
    """Charge uniquement la colonne GWL d'un puit, triée par date."""
    path = os.path.join(DATA_DIR, f"{well_id}.csv")
    df   = pd.read_csv(path, parse_dates=["date"])
    df   = df.sort_values("date").reset_index(drop=True)
    return df["GWL"]


def prepare_well(gwl_series):
    """
    Sépare train/test et vérifie qu'il y a assez de données.
    Retourne (gwl_train, gwl_test) ou None si le puit est inutilisable.
    """
    gwl_train = gwl_series.iloc[:-N_TEST]
    gwl_test  = gwl_series.iloc[-N_TEST:]

    # Vérifie assez de contexte non-NaN dans le train
    if gwl_train.dropna().shape[0] < MIN_CONTEXT:
        return None

    # Vérifie que le test n'est pas entièrement NaN
    if gwl_test.dropna().shape[0] == 0:
        return None

    return gwl_train, gwl_test


# ═══════════════════════════════════════════════════════════════════════════
# 2. PRÉDICTION PAR BATCH
# ═══════════════════════════════════════════════════════════════════════════

def predict_batch(pipeline, contexts):
    """
    Prédit 12 mois pour un batch de séries.
    contexts : liste de tenseurs 1D (longueurs variables)
    Retourne : tableau (batch, N_TEST) de prédictions médianes
    """
    with torch.no_grad():
        forecast = pipeline.predict(contexts, prediction_length=N_TEST)
    # forecast shape : (batch, n_samples, N_TEST)
    # On prend la médiane sur les échantillons
    return np.quantile(forecast.cpu().numpy(), 0.5, axis=1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device : {device}")

    # Chargement du modèle pré-entraîné (téléchargé automatiquement)
    print(f"\nChargement de {MODEL_NAME}...")
    pipeline = BaseChronosPipeline.from_pretrained(
        MODEL_NAME,
        device_map=device,
        torch_dtype=torch.bfloat16,
    )
    print("Modèle chargé.\n")

    # Liste des puits disponibles
    available = [f.replace(".csv", "") for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
    print(f"Chargement de {len(available)} puits...")

    # Prépare les données de tous les puits
    contexts, true_tests, well_ids = [], [], []
    for well_id in available:
        gwl = load_well_gwl(well_id)
        result = prepare_well(gwl)
        if result is None:
            continue
        gwl_train, gwl_test = result

        # Contexte = dernières valeurs non-NaN du train (série continue)
        gwl_context = gwl_train.dropna()
        contexts.append(torch.tensor(gwl_context.values, dtype=torch.float32))
        true_tests.append(gwl_test.values)
        well_ids.append(well_id)

    print(f"Puits utilisables : {len(well_ids)}\n")

    # Prédiction par batch
    print(f"Prédiction en cours (batch={BATCH_SIZE})...")
    all_rmse, all_mae = [], []

    for start in range(0, len(contexts), BATCH_SIZE):
        batch_ctx  = contexts[start:start + BATCH_SIZE]
        batch_true = true_tests[start:start + BATCH_SIZE]

        preds = predict_batch(pipeline, batch_ctx)   # (batch, N_TEST)

        for pred, true in zip(preds, batch_true):
            # Aligne sur les mois non-NaN du test
            mask = ~np.isnan(true)
            if mask.sum() == 0:
                continue
            rmse = np.sqrt(mean_squared_error(true[mask], pred[mask]))
            mae  = mean_absolute_error(true[mask], pred[mask])
            all_rmse.append(rmse)
            all_mae.append(mae)

        if (start // BATCH_SIZE + 1) % 10 == 0:
            print(f"  {start + len(batch_ctx)}/{len(contexts)} puits traités...")

    rmse_arr = np.array(all_rmse)
    mae_arr  = np.array(all_mae)

    # ── Résultats ─────────────────────────────────────────────────────────────
    print(f"\n{'═'*50}")
    print(f"Chronos 2 ({MODEL_NAME.split('/')[-1]}) — {len(rmse_arr)} puits")
    print(f"{'─'*50}")
    print(f"  RMSE moyen  : {rmse_arr.mean():.3f} m  (±{rmse_arr.std():.3f})")
    print(f"  MAE  moyen  : {mae_arr.mean():.3f} m  (±{mae_arr.std():.3f})")
    print(f"  RMSE médian : {np.median(rmse_arr):.3f} m")
    print(f"  MAE  médian : {np.median(mae_arr):.3f} m")
    print(f"{'─'*50}")
    print(f"\nRappel LSTM final :")
    print(f"  RMSE moyen  : 0.805 m  |  RMSE médian : 0.440 m")
    print(f"  MAE  moyen  : 0.686 m  |  MAE  médian : 0.356 m")
    print(f"{'═'*50}")
