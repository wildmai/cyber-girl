MuseTalk inference modules are copied from [TMElyralab/MuseTalk](https://github.com/TMElyralab/MuseTalk) at commit `0a89dec45a0192b824e3cf4daf96c239440c5ed8`.
The MIT license and upstream dependency notices are preserved in LICENSE.
Local changes add explicit model paths and inference devices, load UNet weights through memory mapping and a meta-device model to avoid duplicate allocations, and keep face parsing on CPU.
ResNet weights use a safe local serialization produced during weight preparation, and incomplete audio features raise an error instead of terminating the inference thread.
The application replaces the upstream DWPose/MMCV preprocessing with OpenCV Haar detection for a single still portrait.
This changes crop accuracy and is not equivalent to the upstream landmark preprocessing.
UNet 1.5, VAE, Whisper audio conditioning, positional encoding, face parsing, and face blending remain real model inference.
