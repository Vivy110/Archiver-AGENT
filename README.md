# Archiver Agent Solver 🔐

**Purpose:** Run the agent solver to mint ARCH tokens on Arc Testnet.

## ⚠️ Security Warning

This folder contains files that should **NEVER** be pushed to public repositories:
- `.env` — contains your private key
- `skill.md` — contains the current puzzle seed (prevents others from stealing your mint)

## Files

| File | Description |
|------|-------------|
| `agent_solver.py` | Main solver script |
| `skill.md` | Current puzzle seed (auto-updated on mint) |
| `archiver_abi.json` | Archiver contract ABI |
| `usdc_abi.json` | USDC token ABI |
| `.env.example` | Template for environment variables |
| `.gitignore` | Prevents `.env` from being committed |

## Setup

1. Copy `.env.example` to `.env`:
   ```bash
   copy .env.example .env
   ```

2. Edit `.env` and add your private key:
   ```
   ARCHIVER_PRIVATE_KEY=your_64_char_hex_key
   ```

3. Install dependencies:
   ```bash
   pip install web3 eth-hash
   ```

## Run

```bash
python agent_solver.py
```

The solver will:
1. Read current puzzle seed from `skill.md`
2. Solve the puzzle (multiprocessing)
3. Mint 20,000 ARCH (costs 0.5 USDC)
4. Auto-update `skill.md` with new seed
5. Repeat

## GitHub Safety

This folder is designed to be **safely pushable** to GitHub:
- `.env` is in `.gitignore` — won't be committed
- No private keys in any committed file
- Only the solver code and ABIs — no deployment scripts

## ⚠️ Do NOT Push

If you fork or share this repo:
- NEVER commit `.env`
- NEVER commit `skill.md` (contains current puzzle seed = stealable mint!)
- Keep `skill.md` local only or add it to `.gitignore`