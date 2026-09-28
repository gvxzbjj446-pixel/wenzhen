"""打包后程序的入口（PyInstaller）。"""

import sys

from wenzhen.desktop import main

if __name__ == "__main__":
    sys.exit(main())
