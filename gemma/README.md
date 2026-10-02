# Gemma tools

Use the top-level [installation guide](../docs/INSTALL.md) for dependencies, profiles and model setup. `setupGemma12B.sh` builds pinned llama.cpp and downloads the model only when requested; it never runs the model or auto-tunes GPU settings.

`startGemma.sh` / `stopGemma.sh` invoke the scoped runtime with the shared hardware lease. The songwriter service owns its own resident session and does not require a separately started manual server. `askGemma.sh` sends a manual prompt to an already-running server.

`findMaxNGL.sh` and `testZeroSpill.sh` are retained manual hardware experiments from the original project. They use the current working directory, directly run the model, and are not part of installation or automation. Run them only from the Gemma directory while other model/render workloads are stopped; the spill probe stops an owned manual server before its measurements. Do not use them during an active songwriting campaign.
