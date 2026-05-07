# Archiver Puzzle Configuration

## Contract Info
- **Contract Address:** 0x6379Ef32562a1Cd4715bbF02A05D79fbaBD116Aa
- **USDC Address:** 0x3600000000000000000000000000000000000000
- **Chain:** Arc Testnet (5042002)
- **RPC:** https://rpc.testnet.arc.network
- **Explorer:** https://testnet.arcscan.app

## Current Puzzle
- **Seed:** 0x22e78b5611d75493f2450b9c2afbc4de21cca0d268df21c6990ed4556f11c1e9
- **Hash Target:** keccak256("ARCHIVER_PUZZLE_SEED" + seed + uint64(nonce)) starts with **6 bytes of zero** (0x000000000000...)
- **Mint Amount:** 20,000 ARCH (18 decimals)
- **Mint Cost:** 0.5 USDC (500,000 units, 6 decimals)
- **Avg Solve Time:** 5–10 seconds (multiprocessing, ~10M nonces/worker)

## Puzzle Specification

### Encoding (MUST match Solidity exactly)
```
Python:   keccak256("ARCHIVER_PUZZLE_SEED".encode() + seed_bytes + struct.pack(">Q", nonce))
Solidity: keccak256(abi.encodePacked("ARCHIVER_PUZZLE_SEED", seed, uint64(nonce)))
```

### Difficulty
- 6 leading zero bytes = **48 bits** of difficulty
- Probability per nonce: 1 / 2^48 ≈ 3.55 × 10⁻¹⁵
- Expected nonces searched before finding: ~281 trillion → avg 5–10s on modern CPU

### Nonce Constraints
- Type: uint64 (0 to 2^64 − 1)
- Python: `struct.pack(">Q", nonce)` = big-endian 8 bytes
- Solidity: `uint64(nonce)` = 8 bytes (explicit truncation)

## Solver Setup
```bash
pip install web3
export ARCHIVER_PRIVATE_KEY=0x_your_64_char_hex_private_key
python agent_solver.py
```

## Auto-Update
When `solveAndMint` succeeds, the contract emits:
```
event NewPuzzleSeed(bytes32 indexed newSeed)
```
The Python solver listens for this event and **automatically updates this file** with the new seed — no manual intervention needed between mints.

## Safety
- NEVER share your private key
- The solver auto-approves **infinite USDC** to the Archiver contract (once)
- All mint revenue (USDC) accumulates in the Archiver contract
- Owner can withdraw anytime via `withdrawUSDC(to, amount)`
