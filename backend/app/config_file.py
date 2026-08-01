import os


class ConfigFileManager:
    @staticmethod
    def read(file_path: str) -> dict[str, str]:
        existing: dict[str, str] = {}
        dirpath = os.path.dirname(file_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        if os.path.exists(file_path) and not os.path.isdir(file_path):
            try:
                with open(file_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()
            except (OSError, IOError):
                pass
        return existing

    @staticmethod
    def write(file_path: str, entries: dict[str, str]):
        dirpath = os.path.dirname(file_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        lines = []
        for k, v in entries.items():
            if v:
                if " " in v or "#" in v:
                    lines.append(f'{k}="{v}"')
                else:
                    lines.append(f"{k}={v}")
            else:
                lines.append(f"{k}=")
        lines.append("")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def update(file_path: str, updates: dict[str, str]) -> dict[str, str]:
        existing = ConfigFileManager.read(file_path)
        for key, value in updates.items():
            existing[key] = value
        ConfigFileManager.write(file_path, existing)
        return existing
