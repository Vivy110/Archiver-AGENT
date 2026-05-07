#!/usr/bin/env python3
"""
Archiver Agent Solver v3
- Solves:   keccak256("ARCHIVER_PUZZLE_SEED" + seed + uint64(nonce)) starts with 6 zero bytes
- Auto-approves USDC infinite once
- Listens for NewPuzzleSeed event -> auto-updates skill.md
- Windows-compatible multiprocessing (spawn context)
"""

import os
import sys
import json
import time
import struct
import atexit
import tempfile

import multiprocessing as mp
from pathlib import Path
from typing import Optional

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import TimeExhausted
from web3.middleware import SignAndSendRawMiddlewareBuilder
from eth_hash.auto import keccak

# ============================================================
# Configuration
# ============================================================
SKILL_PATH      = Path(__file__).parent / "skill.md"
SKILL_LOCK_PATH = Path(__file__).parent / "skill.md.lock"
ABI_PATH        = Path(__file__).parent

# Cleanup stale lock file on exit
def _cleanup():
    if SKILL_LOCK_PATH.exists():
        SKILL_LOCK_PATH.unlink()
    tmp_path = SKILL_PATH.with_suffix(".md.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
atexit.register(_cleanup)

CHAIN_ID     = 5042002
RPC_URL      = "https://rpc.testnet.arc.network"
USDC_ADDRESS = Web3.to_checksum_address("0x3600000000000000000000000000000000000000")

MINT_COST   = 500_000         # 0.5 USDC (6 decimals)
MINT_AMOUNT = 20_000 * 10**18  # 20,000 ARCH (18 decimals)
ZERO_PREFIX = bytes(6)        # 6 leading zero bytes
PUZZLE_KW   = b"ARCHIVER_PUZZLE_SEED"
MAX_NONCE   = 2**64 - 1

# Load private key — supports both env var names
PRIVATE_KEY = os.getenv("ARCHIVER_PRIVATE_KEY") or os.getenv("DEPLOYER_PRIVATE_KEY", "")

# ============================================================
# Puzzle Functions
# ============================================================

def hash_puzzle(seed: bytes, nonce: int) -> bytes:
    """
    Compute hash — EXACTLY matches Solidity:
    keccak256(abi.encodePacked("ARCHIVER_PUZZLE_SEED", seed, uint64(nonce)))

    struct.pack('>Q', nonce) = big-endian uint64 = exactly 8 bytes
    Matches Solidity's abi.encodePacked with uint64 nonce.
    """
    data = PUZZLE_KW + seed + struct.pack(">Q", nonce)
    return keccak(data)


def verify_puzzle(seed: bytes, nonce: int) -> bool:
    """Return True if nonce satisfies the 6-byte zero prefix puzzle."""
    return hash_puzzle(seed, nonce)[:len(ZERO_PREFIX)] == ZERO_PREFIX


def solve_chunk(args: tuple) -> Optional[int]:
    """Search nonce range [start, end) for a valid solution. Worker function."""
    seed, start, end = args
    for nonce in range(start, min(end, MAX_NONCE + 1)):
        if verify_puzzle(seed, nonce):
            return nonce
    return None


def solve_puzzle_parallel(
    seed: bytes,
    num_workers: Optional[int] = None,
    chunk_size: int = 500_000_000
) -> Optional[int]:
    """
    Distributed nonce search using multiprocessing.

    uint64 search space is divided across workers.
    Windows uses 'spawn' context for process safety.
    Returns the winning nonce, or None if not found.
    """
    if num_workers is None:
        num_workers = max(1, mp.cpu_count() - 1)

    ranges = [
        (seed, i * chunk_size, min((i + 1) * chunk_size, MAX_NONCE + 1))
        for i in range(num_workers)
    ]

    print(f"[Solver] Workers : {num_workers}")
    print(f"[Solver] Chunk   : {chunk_size:,} nonces/worker")
    print(f"[Solver] Total   : {chunk_size * num_workers:,} nonces")
    print(f"[Solver] Seed    : 0x{seed.hex()}")

    start_time = time.time()

    with mp.Pool(processes=num_workers) as pool:
        for result in pool.imap_unordered(solve_chunk, ranges):
            if result is not None:
                elapsed = time.time() - start_time
                print(f"[Solver] [OK] Found nonce={result:,} in {elapsed:.2f}s")
                pool.terminate()
                pool.join()
                return result

    print("[Solver] [FAIL] No solution found in search space.")
    return None


# ============================================================
# Skill File Management
# ============================================================

def load_skill() -> dict:
    """
    Parse skill.md -> {seed: bytes32, contract_address: str}.
    skill.md must contain:
      - **Contract Address:** <hex address>
      - **Seed:** 0x<64 hex chars>
    """
    if not SKILL_PATH.exists():
        raise FileNotFoundError(
            f"skill.md not found at {SKILL_PATH}\n"
            "Copy from skill.md.template and fill in contract address + seed."
        )

    content = SKILL_PATH.read_text(encoding="utf-8")
    seed, contract_address = None, None

    for line in content.splitlines():
        line = line.strip()
        if "**Seed:**" in line:
            seed_str = line.split("**Seed:**", 1)[-1].strip()
            seed_str = seed_str.lstrip("0x") if not seed_str.startswith("0x") else seed_str[2:]
            seed_str = seed_str.zfill(64)
            seed = bytes.fromhex(seed_str)
        elif "**Contract Address:**" in line:
            contract_address = line.split("**Contract Address:**", 1)[-1].strip()

    if not seed or len(seed) != 32:
        raise ValueError(f"Invalid seed in skill.md (need 32 bytes): {seed}")
    if not contract_address:
        raise ValueError("Contract address missing from skill.md")

    return {
        "seed":             seed,
        "contract_address": Web3.to_checksum_address(contract_address),
    }


def save_skill(seed: bytes, contract_address: str) -> None:
    """
    Atomic update of puzzle seed in skill.md.
    Uses lock file to prevent concurrent writes from multiple processes.
    """
    lock_file = SKILL_LOCK_PATH
    waited = 0

    while lock_file.exists():
        time.sleep(0.1)
        waited += 1
        if waited > 50:
            print("[Skill] [WARN] Lock timeout — writing anyway")
            break

    lock_file.write_text(str(time.time()), encoding="utf-8")
    try:
        content = SKILL_PATH.read_text(encoding="utf-8")
        found_seed = False
        lines = []
        for line in content.splitlines():
            if "**Seed:**" in line:
                found_seed = True
                lines.append(f"- **Seed:** 0x{seed.hex()}")
            else:
                lines.append(line)

        if not found_seed:
            print("[Skill] [WARN] Seed line not found in skill.md — appending")
            lines.append(f"- **Seed:** 0x{seed.hex()}")

        new_content = "\n".join(lines)
        tmp_path = SKILL_PATH.with_suffix(".md.tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(tmp_path, SKILL_PATH)
        print(f"[Skill] [OK] skill.md updated: seed=0x{seed.hex()}")
    finally:
        if lock_file.exists():
            lock_file.unlink()


# ============================================================
# Blockchain Interaction
# ============================================================

def approve_usdc(w3: Web3, usdc: Contract, owner: str, spender: str) -> None:
    """
    Approve Archiver contract to spend USDC (infinite allowance).
    Called once — subsequent calls skip if allowance is already sufficient.
    """
    current = usdc.functions.allowance(owner, spender).call()
    if current >= MINT_COST:
        print(f"[USDC] Allowance OK: {current:,} ≥ {MINT_COST:,}")
        return

    print(f"[USDC] Approving infinite USDC -> {spender} ...")
    tx = usdc.functions.approve(spender, 2**256 - 1).transact({"from": owner})
    receipt = w3.eth.wait_for_transaction_receipt(tx, timeout=30)

    if receipt["status"] == 1:
        print(f"[USDC] [OK] Approved. Tx: {receipt['transactionHash'].hex()}")
    else:
        raise RuntimeError("USDC approval FAILED — check wallet balance")


def mint_architect(
    w3: Web3,
    archiver: Contract,
    nonce: int,
    owner: str
) -> bool:
    """
    Submit solveAndMint(nonce) transaction.

    Pre-flight checks:
      - mintingEnabled must be true
      - remainingSupply must be ≥ MINT_AMOUNT

    On success: extracts NewPuzzleSeed event -> updates skill.md.
    Returns True on success, False on any failure.
    """
    print(f"[Mint] solveAndMint({nonce:,}) ...")

    try:
        # Pre-flight: minting enabled?
        if not archiver.functions.mintingEnabled().call():
            print("[Mint] [FAIL] Minting is DISABLED by owner")
            return False

        # Pre-flight: supply remaining?
        remaining = archiver.functions.remainingSupply().call()
        if remaining < MINT_AMOUNT:
            print(f"[Mint] [FAIL] Max supply reached. Remaining: {remaining:,}")
            return False

        # Submit transaction with explicit gas
        max_priority = w3.eth.max_priority_fee
        tx = archiver.functions.solveAndMint(nonce).transact({
            "from":                owner,
            "maxFeePerGas":        max_priority + 2 * w3.eth.get_block('latest')['baseFeePerGas'],
            "maxPriorityFeePerGas": max_priority,
        })

        # Arc is fast — 2 minute timeout covers 99% of cases
        receipt = w3.eth.wait_for_transaction_receipt(tx, timeout=120)

        if receipt["status"] == 1:
            print(f"[Mint] [OK] SUCCESS! Tx: {receipt['transactionHash'].hex()}")
            print(f"[Mint] Gas used: {receipt['gasUsed']:,}")

            # Extract new seed from NewPuzzleSeed event
            logs = archiver.events.NewPuzzleSeed().process_receipt(receipt)
            if logs:
                new_seed = logs[0]["args"]["newSeed"]
                print(f"[Mint] New seed: 0x{new_seed.hex()}")
                save_skill(new_seed, archiver.address)

            return True
        else:
            print(f"[Mint] [FAIL] FAILED! Tx: {receipt['transactionHash'].hex()}")
            return False

    except TimeExhausted:
        print("[Mint] [FAIL] Transaction timed out — network congestion?")
        return False
    except Exception as e:
        print(f"[Mint] [FAIL] ERROR: {e}")
        return False


# ============================================================
# Main Loop — continuous minting
# ============================================================

def main() -> None:
    global PRIVATE_KEY

    # Required for Windows multiprocessing with spawn context
    mp.freeze_support()

    print("=" * 60)
    print("ARCHIVER AGENT SOLVER  v3")
    print("Arc Testnet  |  Chain ID: 5042002")
    print("=" * 60)

    # ── Validate env ─────────────────────────────────────────
    if not PRIVATE_KEY:
        print("ERROR: Set ARCHIVER_PRIVATE_KEY (or DEPLOYER_PRIVATE_KEY) env var")
        sys.exit(1)

    # ── Connect to Arc ───────────────────────────────────────
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        sys.exit(1)

    print(f"Connected   : {RPC_URL}")
    print(f"Chain ID   : {w3.eth.chain_id}  (expect {CHAIN_ID})")

    if w3.eth.chain_id != CHAIN_ID:
        print("[WARN]️  WARNING: Chain ID mismatch — wrong network?")

    wallet = w3.eth.account.from_key(PRIVATE_KEY)
    w3.middleware_onion.add(
        SignAndSendRawMiddlewareBuilder.build(wallet)
    )
    w3.eth.default_account = wallet.address
    WALLET_ADDRESS = wallet.address
    print(f"Wallet     : {WALLET_ADDRESS}")

    # ── Load skill.md ────────────────────────────────────────
    try:
        skill = load_skill()
        ARCH_ADDRESS  = skill["contract_address"]
        current_seed  = skill["seed"]
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Contract   : {ARCH_ADDRESS}")
    print(f"Seed       : 0x{current_seed.hex()}")

    # ── Load ABIs ───────────────────────────────────────────
    with open(ABI_PATH / "usdc_abi.json") as f:
        usdc_abi = json.load(f)
    with open(ABI_PATH / "archiver_abi.json") as f:
        archiver_abi = json.load(f)

    usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=usdc_abi)
    archiver      = w3.eth.contract(address=ARCH_ADDRESS, abi=archiver_abi)

    # ── Check balances ───────────────────────────────────────
    usdc_bal = usdc_contract.functions.balanceOf(WALLET_ADDRESS).call()
    print(f"USDC Balance: {usdc_bal / 1e6:.2f} USDC")

    if usdc_bal < MINT_COST:
        print(f"ERROR: Need ≥ {MINT_COST / 1e6:.2f} USDC. Get more at https://faucet.circle.com")
        sys.exit(1)

    # ── Approve USDC (once) ─────────────────────────────────
    approve_usdc(w3, usdc_contract, WALLET_ADDRESS, ARCH_ADDRESS)

    arch_bal = archiver.functions.balanceOf(WALLET_ADDRESS).call()
    print(f"ARCH Balance: {arch_bal / 1e18:,.0f} ARCH")

    # ── Continuous minting loop ─────────────────────────────
    print("\n[Solver] Starting continuous puzzle solving loop ...")
    print("Press Ctrl+C to stop.\n")

    solve_count = 0
    error_count = 0

    while True:
        try:
            # Reload seed (might have been updated by another process)
            skill = load_skill()
            current_seed = skill["seed"]

            print(f"\n--- Round {solve_count + 1} ---")
            print(f"[Round] Seed: 0x{current_seed.hex()}")

            # Solve
            nonce = solve_puzzle_parallel(current_seed)

            if nonce is None:
                error_count += 1
                print(f"[Round] No nonce found. Error streak: {error_count}/3")
                if error_count >= 3:
                    print("[Solver] 3 consecutive failures — pausing 60s")
                    time.sleep(60)
                    error_count = 0
                continue

            # Mint
            success = mint_architect(w3, archiver, nonce, WALLET_ADDRESS)

            if success:
                solve_count  += 1
                error_count   = 0
                new_arch_bal  = archiver.functions.balanceOf(WALLET_ADDRESS).call()
                contract_usdc = usdc_contract.functions.balanceOf(ARCH_ADDRESS).call()
                print(f"[Round] ARCH Balance  : {new_arch_bal / 1e18:,.0f} ARCH")
                print(f"[Round] Contract USDC: {contract_usdc / 1e6:.2f} USDC")
                print(f"[Round] Total solves  : {solve_count}")
            else:
                error_count += 1
                print(f"[Round] Mint failed. Error streak: {error_count}")

            time.sleep(2)  # Brief pause between rounds

        except KeyboardInterrupt:
            print(f"\n[Solver] Stopped. Total solves: {solve_count}")
            sys.exit(0)
        except Exception as e:
            error_count += 1
            print(f"[Round] Unexpected error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
