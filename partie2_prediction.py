import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_squared_error, mean_absolute_error

# ─── Chemins ────────────────────────────────────────────────────────────────
DATA_DIR      = os.path.join("data_nappes", "data_nappes", "Data")
OUVRAGES_PATH = os.path.join("data_nappes", "data_nappes", "OUVRAGES.csv")

# ─── Fixés par les consignes ─────────────────────────────────────────────────
SEQ_LEN  = 12                          # mois de contexte donnés au LSTM
N_TEST   = 12                          # mois réservés pour le test (consigne)
FEATURES = ["P", "T", "ET", "NDVI"]   # variables environnementales (consigne)
TARGET   = "GWL"

# ─── Grille de recherche ─────────────────────────────────────────────────────
GRID = {
    "hidden":     [64, 128, 256],
    "n_layers":   [1, 2, 3],
    "lr":         [1e-3, 5e-4, 1e-4],
    "batch_size": [256, 512],
    "epochs":     [30, 50],
}
RESULTS_CSV = "grid_search_results.csv"


# ═══════════════════════════════════════════════════════════════════════════
# 1. CHARGEMENT ET PRÉPARATION DES DONNÉES
# ═══════════════════════════════════════════════════════════════════════════

def load_ouvrages():
    df = pd.read_csv(OUVRAGES_PATH)
    df["Ouvrage"] = df["Ouvrage"].astype(str)
    df["lat_norm"] = (df["Latitude"]  - df["Latitude"].mean())  / df["Latitude"].std()
    df["lon_norm"] = (df["Longitude"] - df["Longitude"].mean()) / df["Longitude"].std()
    return df.set_index("Ouvrage")


def load_well(well_id, ouvrages):
    path = os.path.join(DATA_DIR, f"{well_id}.csv")
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["lat_norm"] = ouvrages.loc[well_id, "lat_norm"]
    df["lon_norm"] = ouvrages.loc[well_id, "lon_norm"]
    return df


def normalize_well(df):
    """Z-score par puit. Retourne le df normalisé + les stats pour inverser."""
    stats = {}
    df = df.copy()
    for col in FEATURES + [TARGET]:
        mean = df[col].mean()
        std  = df[col].std()
        std  = std if std > 0 else 1.0
        df[col] = (df[col] - mean) / std
        stats[col] = (mean, std)
    return df, stats


def make_sequences(df, seq_len):
    """
    Crée des séquences glissantes de longueur seq_len.
    Input  : [P, T, ET, NDVI, lat_norm, lon_norm] à t-seq_len .. t-1
    Target : GWL à t
    """
    all_features = FEATURES + ["lat_norm", "lon_norm"]
    cols = all_features + [TARGET]
    df_clean = df.dropna(subset=cols).reset_index(drop=True)

    X, y = [], []
    for i in range(seq_len, len(df_clean)):
        X.append(df_clean[all_features].iloc[i - seq_len:i].values)
        y.append(df_clean[TARGET].iloc[i])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def build_dataset(ouvrages, max_wells=None):
    """
    Charge tous les puits, découpe train/test, retourne :
      - X_train, y_train : séquences d'entraînement (tous puits)
      - test_data        : liste de (well_id, X_test, y_test, gwl_stats)
    """
    available = [f.replace(".csv", "") for f in os.listdir(DATA_DIR)]
    available = [w for w in available if w in ouvrages.index]
    if max_wells:
        available = available[:max_wells]

    X_train_all, y_train_all = [], []
    test_data = []

    print(f"Chargement de {len(available)} puits...")
    for i, well_id in enumerate(available):
        if i % 200 == 0:
            print(f"  {i}/{len(available)}")

        df = load_well(well_id, ouvrages)

        # Sépare train / test AVANT normalisation
        df_train = df.iloc[:-N_TEST]
        df_test  = df.iloc[-(N_TEST + SEQ_LEN):]   # inclut SEQ_LEN mois de contexte

        # Normalise sur les stats du train uniquement
        df_train_norm, stats = normalize_well(df_train)
        df_test_norm,  _     = normalize_well(df_test)

        X_tr, y_tr = make_sequences(df_train_norm, SEQ_LEN)
        X_te, y_te = make_sequences(df_test_norm,  SEQ_LEN)

        if len(X_tr) == 0 or len(X_te) == 0:
            continue

        X_train_all.append(X_tr)
        y_train_all.append(y_tr)
        test_data.append((well_id, X_te, y_te, stats[TARGET]))

    X_train = np.concatenate(X_train_all, axis=0)
    y_train = np.concatenate(y_train_all, axis=0)
    return X_train, y_train, test_data


# ═══════════════════════════════════════════════════════════════════════════
# 3. MODÈLE LSTM
# ═══════════════════════════════════════════════════════════════════════════

class LSTMModel(nn.Module):
    def __init__(self, input_size, hidden_size, n_layers):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size  = input_size,
            hidden_size = hidden_size,
            num_layers  = n_layers,
            batch_first = True,
            dropout     = 0.2 if n_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        # x : (batch, seq_len, features)
        out, _ = self.lstm(x)
        # Sortie du dernier pas de temps uniquement
        return self.fc(out[:, -1, :]).squeeze(-1)


# ═══════════════════════════════════════════════════════════════════════════
# 4. ENTRAÎNEMENT
# ═══════════════════════════════════════════════════════════════════════════

def train(model, X_gpu, y_gpu, batch_size, optimizer, criterion):
    """Batching manuel depuis la VRAM — aucun transfert CPU→GPU pendant l'entraînement."""
    model.train()
    total_loss = 0
    n = len(X_gpu)
    indices = torch.randperm(n, device=X_gpu.device)

    for start in range(0, n, batch_size):
        idx = indices[start:start + batch_size]
        X_batch, y_batch = X_gpu[idx], y_gpu[idx]
        optimizer.zero_grad()
        pred = model(X_batch)
        loss = criterion(pred, y_batch)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(idx)

    return total_loss / n


# ═══════════════════════════════════════════════════════════════════════════
# 5. ÉVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate(model, test_data, device):
    model.eval()
    all_rmse, all_mae = [], []

    with torch.no_grad():
        for _, X_te, y_te, (gwl_mean, gwl_std) in test_data:
            X_tensor  = torch.tensor(X_te).to(device)
            pred_norm = model(X_tensor).cpu().numpy()

            # Inverse normalisation → valeurs en mètres réels
            pred_real = pred_norm * gwl_std + gwl_mean
            true_real = y_te      * gwl_std + gwl_mean

            rmse = np.sqrt(mean_squared_error(true_real, pred_real))
            mae  = mean_absolute_error(true_real, pred_real)
            all_rmse.append(rmse)
            all_mae.append(mae)

    return np.array(all_rmse), np.array(all_mae)


# ═══════════════════════════════════════════════════════════════════════════
# 6. MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device : {device}")

    ouvrages = load_ouvrages()

    # Chargement unique des données (commun à toutes les configs)
    X_train, y_train, test_data = build_dataset(ouvrages, max_wells=None)
    print(f"\nDonnées d'entraînement : {X_train.shape[0]} séquences")
    print(f"Puits de test          : {len(test_data)}\n")

    # ── Meilleure config issue du grid search ────────────────────────────────
    BEST = {"hidden": 128, "n_layers": 3, "lr": 5e-4, "batch_size": 512, "epochs": 30}

    input_size = len(FEATURES) + 2
    criterion  = nn.MSELoss()

    print("Chargement des données sur la VRAM...")
    X_gpu = torch.tensor(X_train).to(device)
    y_gpu = torch.tensor(y_train).to(device)
    vram  = X_gpu.element_size() * X_gpu.nelement() / 1e9
    print(f"Dataset GPU : {X_gpu.shape[0]} séquences — {vram:.2f} Go\n")

    model     = LSTMModel(input_size, BEST["hidden"], BEST["n_layers"]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=BEST["lr"])

    n_params = sum(p.numel() for p in model.parameters())
    print(f"Modèle final : hidden={BEST['hidden']} | layers={BEST['n_layers']} | "
          f"lr={BEST['lr']} | batch={BEST['batch_size']} | epochs={BEST['epochs']}")
    print(f"Paramètres   : {n_params}\n")

    for epoch in range(1, BEST["epochs"] + 1):
        loss = train(model, X_gpu, y_gpu, BEST["batch_size"], optimizer, criterion)
        if epoch % 5 == 0 or epoch == 1:
            print(f"Epoch {epoch:3d}/{BEST['epochs']} — Loss: {loss:.4f}")

    torch.save(model.state_dict(), "lstm_final.pt")
    print("\nModèle sauvegardé : lstm_final.pt")

    print("\nÉvaluation sur les 12 derniers mois de chaque puit...")
    rmse_arr, mae_arr = evaluate(model, test_data, device)

    print(f"\n{'═'*45}")
    print(f"Résultats finaux sur {len(rmse_arr)} puits :")
    print(f"  RMSE moyen  : {rmse_arr.mean():.3f} m  (±{rmse_arr.std():.3f})")
    print(f"  MAE  moyen  : {mae_arr.mean():.3f} m  (±{mae_arr.std():.3f})")
    print(f"  RMSE médian : {np.median(rmse_arr):.3f} m")
    print(f"  MAE  médian : {np.median(mae_arr):.3f} m")
    print(f"{'═'*45}")

