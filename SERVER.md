# Running the flop-solve library builder on a Linux server

Instructions for Claude Code (or a person) on a Linux server with a terminal.

1. **Get the project**:
   `git clone https://github.com/coldbrewtroll/Poker-trainer.git ~/Poker-trainer` (or
   `git pull` in it if it's already there).
2. **Python**: Python 3.10 or newer, plus numpy: `python3 -m pip install --user numpy`
   (or the distro's `python3-numpy`).
3. **Build the solver** (TexasSolver's console program, the same version the trainer uses):
   ```
   sudo apt-get install -y build-essential cmake git      # (or the distro's equivalent)
   git clone https://github.com/bupticybee/TexasSolver.git ~/TexasSolver
   cd ~/TexasSolver && git checkout 02c8021
   mkdir -p build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. && make -j"$(nproc)" console_solver
   mkdir -p ~/Poker-trainer/solver_bin
   cp ~/TexasSolver/build/console_solver ~/Poker-trainer/solver_bin/
   cp -r ~/TexasSolver/resources ~/Poker-trainer/solver_bin/
   ```
   Check: `ls ~/Poker-trainer/solver_bin/resources/compairer/card5_dic_sorted.txt` must exist.
4. **Check the queue**: `cd ~/Poker-trainer && python3 solver/library_builder.py --list --depths 40,30,60,50`
   (about 4,400 flops: 30, 40, 50 and 60 bb, 16 spots, 78 flops each).
5. **Start it**, in a way that survives logging out:
   ```
   cd ~/Poker-trainer
   nohup python3 solver/library_builder.py --depths 40,30,60,50 > library.out 2>&1 &
   ```
   It works out how many solves to run side by side from the cores and memory, runs at
   low priority, skips flops already solved, and saves each one to
   `postflop/<depth>bb_<spot>/<flop>.json`. Progress: `tail -f library.out` (or
   `solver_runs/library.log`). To stop: `pkill -f library_builder`; run step 5 again to
   carry on.
6. **Send the solves back** (every day or two): commit only the new `postflop/` files to
   a branch called `server-solves` and push it:
   ```
   cd ~/Poker-trainer
   git checkout -B server-solves
   git add postflop && git commit -m "Server flop solves" && git push -u origin server-solves
   ```
   (Pushing needs a GitHub login once: `gh auth login`, or a personal access token.)
   Don't change anything else in the repo: the ranges, the charts and the code stay as they are.
