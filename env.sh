# Użycie: source env.sh   (aktywuje env conda fishsofa i ścieżki SOFA)
export SOFA_ROOT="$HOME/sofa/SOFA_v26.06.00_Linux"
_sp="$SOFA_ROOT/plugins"
export PYTHONPATH="$_sp/SofaPython3/lib/python3/site-packages:$_sp/SoftRobots/lib/python3/site-packages:$_sp/STLIB/lib/python3/site-packages:$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$HOME/sofa/fishsofa-pylib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
unset _sp
conda activate fishsofa
