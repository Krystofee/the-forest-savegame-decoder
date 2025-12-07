# The Forest Savegame Decoder

A Python toolkit for decoding, editing, and transferring player data in **The Forest** game save files.

## Features

- **Decode** Unity Serializer format (SerV10) used by The Forest
- **View and edit** building health values
- **Extract** host player data from `__RESUME__` to CS format
- **Inject** CS player data into `__RESUME__` files
- **Transfer** player data between saves

## Save File Structure

The Forest uses Unity's LevelSerializer format:

| File | Description |
|------|-------------|
| `__RESUME__` | Main save file (base64 encoded) containing world data + host player |
| `cs/<guid>` | Client save files containing joining player data |
| `info` | Game stats (days survived, kills, etc.) |

## Installation

```bash
# Clone the repository
git clone https://github.com/Krystofee/the-forest-savegame-decoder.git
cd the-forest-savegame-decoder

# Create virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# No external dependencies required - uses only Python standard library
```

## Usage

### Analyze Save Files

```bash
# Full analysis of a save file
python forest_decoder.py Slot1/__RESUME__ -v

# Export analysis to JSON
python forest_decoder.py Slot1/__RESUME__ --output analysis.json

# Export fully decoded binary
python forest_decoder.py Slot1/__RESUME__ --decode-only decoded.bin
```

### Edit Building Health

```bash
# List all buildings and their HP
python forest_editor.py Slot1/__RESUME__ --list

# Heal all buildings to max HP
python forest_editor.py Slot1/__RESUME__ --heal-all --save

# Repair damaged buildings (HP < 50)
python forest_editor.py Slot1/__RESUME__ --repair 50 --save

# Set specific building HP
python forest_editor.py Slot1/__RESUME__ --set-hp 0 500 --save

# Set all buildings to custom HP
python forest_editor.py Slot1/__RESUME__ --set-all-hp 999 --save
```

### Transfer Player Data

```bash
# Extract host player from __RESUME__ to CS file
python forest_player_swap.py extract Slot1/__RESUME__ -o host_player.cs

# Inject CS player data into __RESUME__
python forest_player_swap.py inject Slot1/__RESUME__ -c cs/player-guid -o Slot1/__RESUME__

# Compare player data between files
python forest_player_swap.py compare Slot1/__RESUME__ cs/player-guid
```

### Example: Transfer Character Between Saves

```bash
# Extract your character from old save
python forest_player_swap.py extract OldSave/__RESUME__ -o my_character.cs

# Import character into new save
python forest_player_swap.py inject NewSave/__RESUME__ -c my_character.cs
```

## Player Data Transferred

The tools handle all player-specific data:

- **Inventory**: InventoryItemView, DecayingInventoryItemView
- **Upgrades**: UpgradeViewReceiver (weapon upgrades)
- **Player State**: HeldItemsData, PlayerClothing, ActiveAreaInfo
- **Progress**: SurvivalBookBestiary, PassengerManifest, TickOffSystem
- **Special Items**: MapInventoryItemView, WaterSkinInventoryItemView

## File Descriptions

| File | Description |
|------|-------------|
| `forest_decoder.py` | Full recursive decoder for save file analysis |
| `forest_editor.py` | Building health editor with save capability |
| `forest_player_swap.py` | Player data extraction and injection tool |

## Technical Details

The Forest uses Unity's **LevelSerializer** (UnitySerializer) for save files:

- Outer layer: Base64 encoded
- Inner structure: `NOCOMPRESSION` + Base64 encoded level data
- Format version: SerV10
- Objects identified by GUIDs and type tokens

## Safety

- All tools create automatic backups before modifying files
- Use `--output` flag to write to a different file
- Test modifications on copies first

## License

MIT License

## Credits

- Save format reverse-engineered from [UnitySerializer-ng](https://github.com/TheSniperFan/unityserializer-ng)
- Inspired by the discontinued [ModAPI Save Editor](https://modapi.survivetheforest.net/)
