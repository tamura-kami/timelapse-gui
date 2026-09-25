# タイムラプス撮影 GUI

Ubuntu のデスクトップで `fswebcam` を使って定間隔撮影する GTK4 アプリです。撮影画像は `YYYYMMDD_HHMMSS.jpg` の名前で保存され、同じ秒に複数枚撮影した場合はミリ秒を付けて区別します。画面に撮影状態・枚数・最新画像を表示します。

## 必要パッケージ

```sh
sudo apt update
sudo apt install fswebcam python3-gi gir1.2-gtk-4.0 python3-venv
```

プロジェクト直下に仮想環境を作成します。GTK4 と PyGObject は Ubuntu の APT パッケージを使うため、`--system-site-packages` を指定します。すでに `venv/` がある場合は作成をスキップできます。

```sh
python3 -m venv --system-site-packages ./venv
```

## カメラの確認

アプリを起動する前に、ターミナルから `fswebcam` がカメラを使えることを確認してください。

```sh
mkdir -p ~/Pictures/timelapse
fswebcam --no-banner --resolution 1280x720 ~/Pictures/timelapse/camera-check.jpg
```

コマンドが成功し、`camera-check.jpg` が作成されれば利用できます。デバイス権限やカメラの使用中などの理由で失敗した場合は、`fswebcam` のエラーを確認してください。

## 起動

プロジェクトディレクトリで次を実行します。

```sh
./venv/bin/python main.py
```

保存先の初期値は `~/Pictures/timelapse/YYYYMMDD-HHMM`（アプリ起動時刻）です。画面で保存先と撮影間隔（0.5秒刻み、最小0.5秒）を設定して「撮影開始」を押すと、直ちに1枚撮影し、その後は指定した間隔で撮影します。撮影間隔は `~/.config/timelapse-gui/settings.json` に保存され、次回起動時に復元されます。撮影中は設定を変更できません。「撮影停止」で次回以降の撮影を停止します。
