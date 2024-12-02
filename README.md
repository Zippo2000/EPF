# E-paper ESP32 Frame

- **This project is currently a Work in Progress (WIP)!**

This project leverages **Immich** as a service for organizing albums and photos. Photos intended for display are grouped into specific albums, and a FLASK server hosted on a NAS or cloud server handles image cropping and editing before sending them to the ESP32. Since the ESP32 remains in deep sleep most of the time, and all image processing is handled by the server, the EPD updates photos very quickly, typically within 15 seconds. This significantly reduces power consumption.

## Features

- **Customizable Update Interval**: Adjust the photo update interval by entering the setting mode via the SETTING PIN on the ESP32 (WIP).
- **Fully Automated Photo Management**: Manage photos through Immich without additional manual processes; photos will automatically sync to the frame.
- **Ultra-low Power Consumption**: As all image processing and quantization are handled by the server, the device only consumes ~16µA during deep sleep, with photo updates completed within 15 seconds.
- **Customizable Display**: Configure photo orientation, basic color adjustments, album name, server IP, and other settings via the `config/config.yaml` file.

## Table of Contents

- [Components](#components)
- [Installation](#installation)
- [License](#license)

## Components

- [FireBeetle 2 ESP32-C6](https://www.dfrobot.com/product-2771.html)
- [7.3-inch E Ink Spectra 6 (E6) Full Color E-Paper Display Module + HAT](https://www.waveshare.com/7.3inch-e-paper-hat-e.htm)
- Picture frame: A standard picture frame that accommodates the e-paper frame.
- Li-Po battery with PH2.0 header
- Simple button * 2 for reset and setting

## Installation

### Clone the Repository

```bash
$ git clone https://github.com/jwchen119/epf.git
```

### Manually Build Docker Image

```bash
$ git clone https://github.com/jwchen119/epf.git
$ docker build -t jwchen119/epf .
```

### Download Precompiled Docker Image

If you prefer not to build the image yourself, you can download the precompiled image from [DockerHub](https://hub.docker.com/r/jwchen119/epf):

```bash
docker pull jwchen119/epf
```

### Run the Container

Create a container from the image. Don’t forget to edit your configuration path and Immich API key.

```bash
$ docker run --name epf -e IMMICH-API-KEY='<replace-your-immich-api-key>' -v <replace-to-your-path>:/app/config -d -p <replace-port>:5000 jwchen119/epf
```

### Configure `config.yaml`

Below is an example of a configured `config.yaml` file:

```yaml
immich:
  # Album name, must match the album name created in Immich
  album: testAlbme
  # Photo rotation angle, accepts only (0, 90, 180, 270)
  rotation: 270
  # Immich server URL
  url: http://192.168.100.36:2283
  # Color(Saturation) enhancement level using PIL's ImageEnhance.Color (1.0 = original level)
  enhanced: 1.5
  # Contrast level using PIL's ImageEnhance.Contrast (1.0 = original level)
  contrast: 1.2
```

### ESP32-C6

Connect the EPD, ESP32-C6, Li-Po battery, and setting button according to the correct wiring configuration. Then, upload the Arduino code from the project directory to the ESP32-C6. On the first boot, connect to the ESP32-C6’s Wi-Fi hotspot to configure Wi-Fi and the Immich server. You can re-enter the configuration page later by short-circuiting the setting button and rebooting.

## License

This project is licensed under the MIT License.

