# CHAT_3_ARTIFACT_MANIFEST.md

Every artifact that must be preserved for the next chat. Checksums are SHA-256 computed at the end of Chat 3. The five approved patches and the three approved reports were checksummed before and after the handoff work and are unchanged.
This manifest cannot contain its own checksum.

Base: `14751b09a278830c0803993f0b4fafde6bff34e9` (`origin/main` = same at freeze time). Approved chain: 4.4 -> 4.5 -> 4.6 -> 4.7b -> 4.8 (757 tests). Step 4.9: IN PROGRESS / NOT APPROVED / NOT COMPLETE.

| # | filename | step | required | bytes | sha256 | purpose |
|---|---|---|---|---|---|---|
| 1 | `step_4_4_getPlayerMarketValue.patch` | 4.4 | REQUIRED | 61568 | `237c1f5febab14e7ef0ab168360d1ae23e81e70d1d88ddc4de33abf95ed7ec9c` | Patch 1 of 5: getPlayerMarketValue (apply first, on a clean 14751b0) |
| 2 | `step_4_5_getPlayerInjuries.patch` | 4.5 | REQUIRED | 68477 | `177e1c5b72baf3ab974752e723cfbb6043a9fa1785e35bbad0f315f76c045566` | Patch 2 of 5: getPlayerInjuries |
| 3 | `step_4_6_getPlayerEaAttributes.patch` | 4.6 | REQUIRED | 61353 | `388ab30db3df2db76e7d2a35dee71601145a45ee41af8b83762aeb057cc63765` | Patch 3 of 5: getPlayerEaAttributes |
| 4 | `step_4_7b_G13_remove_public_uid.patch` | 4.7b | REQUIRED | 71633 | `3ad4fda4d67c1c9470c075f9369811c14f8daf16b9ea408bfab93d1b63e518f0` | Patch 4 of 5: G13 resolved, canonical_player_uid removed from the public contract (0.5.0-draft) |
| 5 | `step_4_8_getPlayerLineage.patch` | 4.8 | REQUIRED | 93431 | `186f2e413cfaec6d23a9482fe0fb6671b70023a117eac2f02194198f281de2b9` | Patch 5 of 5: getPlayerLineage (last approved step) |
| 6 | `STEP_4_7a_G13_DECISION.md` | 4.7a | REQUIRED | 11041 | `eb4522f11cbc4b59c28d86b800670a0a63a04825979452bf1ceb8e0e41add0d8` | Approved decision memo for G13 (options A/B/C, owner chose A) |
| 7 | `STEP_4_7b_G13_REPORT.md` | 4.7b | REQUIRED | 9212 | `8360bfc0dd681abd72a5ea3018af72a1e22dc0a0f322d49c69893ede3d124d19` | Approved report of Step 4.7b |
| 8 | `STEP_4_8_REPORT.md` | 4.8 | REQUIRED | 20183 | `1dfd1ab27ce8a7af0645de9634bd8f72d6dad99fff52db4895932e3e3e2aebd3` | Approved report of Step 4.8 (corrected version: 51 scanned responses) |
| 9 | `WIP_step_4_9_INCOMPLETE_NOT_APPROVED.patch` | 4.9 (WIP) | REQUIRED to resume 4.9 (not an approved artifact) | 45775 | `622fadeb7bb112000324827fdb44b67c255fba9bd573e24a399f21c1d33a697b` | Step 4.9 work in progress (5 files, +482/-5); NOT complete, NOT approved, apply AFTER the five patches; known state: 795 passed, 1 failed |
| 10 | `CHAT_3_HANDOFF.md` | handoff | REQUIRED | 21273 | `41e505c49730bf255e9497b9952a3dad94ef987210875f56a9e73cf70d20259d` | Full handoff report of Chat 3 |
| 11 | `NEXT_CHAT_INSTRUCTIONS.md` | handoff | REQUIRED | 6765 | `40d57f74c14fb690061f5454d567865fc14b8f2d32f832fe43c82836c7e7150b` | Ordered procedure for the next chat (first line: resume Step 4.9, do not restart 4.8, do not begin 4.10) |
| 12 | `mutation_scripts/mutate_step_4_4.py` | 4.4 | recommended | 6656 | `6766ffe0ad79c63d41333fafe2fdf1670b38325f961e5c17c0d3d305c7bb12cc` | Mutation script, 26 mutations (ROOT /tmp/mut) |
| 13 | `mutation_scripts/mutate_step_4_5.py` | 4.5 | recommended | 7804 | `d8214a8cbbe5c34623985f3007f0079ea35e4697c9c7a599a0000fac7f68de37` | Mutation script, 30 mutations (ROOT /tmp/mut5) |
| 14 | `mutation_scripts/mutate_step_4_6.py` | 4.6 | recommended | 7440 | `961f6a7daa843cd9c00b3f388a039bb5b491acd815cc2121e865c4c16371b167` | Mutation script, 29 mutations (ROOT /tmp/mut6) |
| 15 | `mutation_scripts/mutate_step_4_7b.py` | 4.7b | recommended | 5869 | `2f5826b92fff6c05daac95a4b71121d9f260afac55e78f4edab46f99e1e5ac45` | Mutation script, 16 mutations (ROOT /tmp/mut7) |
| 16 | `mutation_scripts/mutate_step_4_8.py` | 4.8 | recommended | 9448 | `9220ca22da16b6cb1bf845b86b709a0d3b9170127f9556b7da562168c58dc6d8` | Mutation script, 38 mutations (ROOT /tmp/mut8) |
| 17 | `mutation_scripts/mutate_step_4_9_WIP.py` | 4.9 (WIP) | REQUIRED to resume 4.9 | 7111 | `a4b7f7b0a19c84d6bccbf9a526e7c510da503367aead5bfecd775a0b3b86ab67` | Mutation script for the Step 4.9 work in progress, 27 mutations (ROOT /tmp/mut9); needed to finish 4.9 |

## Not present (do not assume they exist)

| filename | state |
|---|---|
| `STEP_4_4_REPORT.md` | NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3 |
| `STEP_4_5_REPORT.md` | NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3 |
| `STEP_4_6_REPORT.md` | NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3 |
| `STEP_4_9_REPORT.md` | NOT WRITTEN: Step 4.9 is not complete |
| `step_4_9_integration_consistency.patch` | NOT BUILT: only the WIP patch exists |

## Apply order (clean `14751b0`, `git apply --whitespace=error`)

1. `step_4_4_getPlayerMarketValue.patch`
2. `step_4_5_getPlayerInjuries.patch`
3. `step_4_6_getPlayerEaAttributes.patch`
4. `step_4_7b_G13_remove_public_uid.patch`
5. `step_4_8_getPlayerLineage.patch`  -> 757 passed
6. (only to resume Step 4.9) `WIP_step_4_9_INCOMPLETE_NOT_APPROVED.patch`  -> 796 collected, 795 passed, 1 failed (known)

## Machine-readable manifest

```json
{
  "base_commit": "14751b09a278830c0803993f0b4fafde6bff34e9",
  "origin_main_at_freeze": "14751b09a278830c0803993f0b4fafde6bff34e9",
  "last_approved_step": "4.8",
  "step_4_9": "IN_PROGRESS_NOT_APPROVED_NOT_COMPLETE",
  "tests_approved_chain": 757,
  "tests_working_tree": {
    "collected": 796,
    "passed": 795,
    "failed": 1,
    "known_failure": "tests/test_contract/test_architecture_doc.py::test_gap_register_g1_to_g17"
  },
  "artifacts": [
    {
      "filename": "step_4_4_getPlayerMarketValue.patch",
      "purpose": "Patch 1 of 5: getPlayerMarketValue (apply first, on a clean 14751b0)",
      "required": "REQUIRED",
      "step": "4.4",
      "bytes": 61568,
      "sha256": "237c1f5febab14e7ef0ab168360d1ae23e81e70d1d88ddc4de33abf95ed7ec9c"
    },
    {
      "filename": "step_4_5_getPlayerInjuries.patch",
      "purpose": "Patch 2 of 5: getPlayerInjuries",
      "required": "REQUIRED",
      "step": "4.5",
      "bytes": 68477,
      "sha256": "177e1c5b72baf3ab974752e723cfbb6043a9fa1785e35bbad0f315f76c045566"
    },
    {
      "filename": "step_4_6_getPlayerEaAttributes.patch",
      "purpose": "Patch 3 of 5: getPlayerEaAttributes",
      "required": "REQUIRED",
      "step": "4.6",
      "bytes": 61353,
      "sha256": "388ab30db3df2db76e7d2a35dee71601145a45ee41af8b83762aeb057cc63765"
    },
    {
      "filename": "step_4_7b_G13_remove_public_uid.patch",
      "purpose": "Patch 4 of 5: G13 resolved, canonical_player_uid removed from the public contract (0.5.0-draft)",
      "required": "REQUIRED",
      "step": "4.7b",
      "bytes": 71633,
      "sha256": "3ad4fda4d67c1c9470c075f9369811c14f8daf16b9ea408bfab93d1b63e518f0"
    },
    {
      "filename": "step_4_8_getPlayerLineage.patch",
      "purpose": "Patch 5 of 5: getPlayerLineage (last approved step)",
      "required": "REQUIRED",
      "step": "4.8",
      "bytes": 93431,
      "sha256": "186f2e413cfaec6d23a9482fe0fb6671b70023a117eac2f02194198f281de2b9"
    },
    {
      "filename": "STEP_4_7a_G13_DECISION.md",
      "purpose": "Approved decision memo for G13 (options A/B/C, owner chose A)",
      "required": "REQUIRED",
      "step": "4.7a",
      "bytes": 11041,
      "sha256": "eb4522f11cbc4b59c28d86b800670a0a63a04825979452bf1ceb8e0e41add0d8"
    },
    {
      "filename": "STEP_4_7b_G13_REPORT.md",
      "purpose": "Approved report of Step 4.7b",
      "required": "REQUIRED",
      "step": "4.7b",
      "bytes": 9212,
      "sha256": "8360bfc0dd681abd72a5ea3018af72a1e22dc0a0f322d49c69893ede3d124d19"
    },
    {
      "filename": "STEP_4_8_REPORT.md",
      "purpose": "Approved report of Step 4.8 (corrected version: 51 scanned responses)",
      "required": "REQUIRED",
      "step": "4.8",
      "bytes": 20183,
      "sha256": "1dfd1ab27ce8a7af0645de9634bd8f72d6dad99fff52db4895932e3e3e2aebd3"
    },
    {
      "filename": "WIP_step_4_9_INCOMPLETE_NOT_APPROVED.patch",
      "purpose": "Step 4.9 work in progress (5 files, +482/-5); NOT complete, NOT approved, apply AFTER the five patches; known state: 795 passed, 1 failed",
      "required": "REQUIRED to resume 4.9 (not an approved artifact)",
      "step": "4.9 (WIP)",
      "bytes": 45775,
      "sha256": "622fadeb7bb112000324827fdb44b67c255fba9bd573e24a399f21c1d33a697b"
    },
    {
      "filename": "CHAT_3_HANDOFF.md",
      "purpose": "Full handoff report of Chat 3",
      "required": "REQUIRED",
      "step": "handoff",
      "bytes": 21273,
      "sha256": "41e505c49730bf255e9497b9952a3dad94ef987210875f56a9e73cf70d20259d"
    },
    {
      "filename": "NEXT_CHAT_INSTRUCTIONS.md",
      "purpose": "Ordered procedure for the next chat (first line: resume Step 4.9, do not restart 4.8, do not begin 4.10)",
      "required": "REQUIRED",
      "step": "handoff",
      "bytes": 6765,
      "sha256": "40d57f74c14fb690061f5454d567865fc14b8f2d32f832fe43c82836c7e7150b"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_4.py",
      "purpose": "Mutation script, 26 mutations (ROOT /tmp/mut)",
      "required": "recommended",
      "step": "4.4",
      "bytes": 6656,
      "sha256": "6766ffe0ad79c63d41333fafe2fdf1670b38325f961e5c17c0d3d305c7bb12cc"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_5.py",
      "purpose": "Mutation script, 30 mutations (ROOT /tmp/mut5)",
      "required": "recommended",
      "step": "4.5",
      "bytes": 7804,
      "sha256": "d8214a8cbbe5c34623985f3007f0079ea35e4697c9c7a599a0000fac7f68de37"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_6.py",
      "purpose": "Mutation script, 29 mutations (ROOT /tmp/mut6)",
      "required": "recommended",
      "step": "4.6",
      "bytes": 7440,
      "sha256": "961f6a7daa843cd9c00b3f388a039bb5b491acd815cc2121e865c4c16371b167"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_7b.py",
      "purpose": "Mutation script, 16 mutations (ROOT /tmp/mut7)",
      "required": "recommended",
      "step": "4.7b",
      "bytes": 5869,
      "sha256": "2f5826b92fff6c05daac95a4b71121d9f260afac55e78f4edab46f99e1e5ac45"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_8.py",
      "purpose": "Mutation script, 38 mutations (ROOT /tmp/mut8)",
      "required": "recommended",
      "step": "4.8",
      "bytes": 9448,
      "sha256": "9220ca22da16b6cb1bf845b86b709a0d3b9170127f9556b7da562168c58dc6d8"
    },
    {
      "filename": "mutation_scripts/mutate_step_4_9_WIP.py",
      "purpose": "Mutation script for the Step 4.9 work in progress, 27 mutations (ROOT /tmp/mut9); needed to finish 4.9",
      "required": "REQUIRED to resume 4.9",
      "step": "4.9 (WIP)",
      "bytes": 7111,
      "sha256": "a4b7f7b0a19c84d6bccbf9a526e7c510da503367aead5bfecd775a0b3b86ab67"
    }
  ],
  "absent": [
    {
      "filename": "STEP_4_4_REPORT.md",
      "status": "NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3",
      "step": "4.4"
    },
    {
      "filename": "STEP_4_5_REPORT.md",
      "status": "NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3",
      "step": "4.5"
    },
    {
      "filename": "STEP_4_6_REPORT.md",
      "status": "NOT FILED: exists only as chat text; essential content is in CHAT_3_HANDOFF.md section 3",
      "step": "4.6"
    },
    {
      "filename": "STEP_4_9_REPORT.md",
      "status": "NOT WRITTEN: Step 4.9 is not complete",
      "step": "4.9"
    },
    {
      "filename": "step_4_9_integration_consistency.patch",
      "status": "NOT BUILT: only the WIP patch exists",
      "step": "4.9"
    }
  ]
}
```
