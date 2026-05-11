# GigaAM builds its model from a Hydra/OmegaConf config whose `_target_`
# strings reference submodules dynamically (e.g. gigaam.encoder.ConformerEncoder,
# gigaam.decoder.*). PyInstaller's static analysis never sees those string
# references, so without this hook the submodules are dropped from the bundle and
# the frozen app dies at startup with:
#   Error locating target 'gigaam.encoder.ConformerEncoder'
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("gigaam")
datas = collect_data_files("gigaam")
