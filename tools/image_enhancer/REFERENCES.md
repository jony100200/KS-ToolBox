# Research, Algorithmic Foundations & References

This document records the foundational research, algorithms, and references studied and synthesized into the KS Image Enhancer suite.

---

## 1. Classical Signal & Image Processing Algorithms

### A. Multi-Scale Retinex with Color Restoration (MSRCR)
* **Foundational Papers:**
  * Jobson, D. J., Rahman, Z., & Woodell, G. A. (1997). *A multiscale retinex for bridging the gap between color images and the human observation of scenes.* IEEE Transactions on Image Processing, 6(7), 965-976.
  * Jobson, D. J., Rahman, Z., & Woodell, G. A. (1997). *Properties and performance of a multiscale retinex.* IEEE Transactions on Image Processing, 6(7), 1003-1015.
* **Core Formulation:**
  Multi-scale surround logarithmic subtraction:
  $$R_i(x, y) = \sum_{k=1}^{K} w_k \left( \log(I_i(x, y) + 1) - \log(I_i(x, y) * G_k(x, y) + 1) \right)$$
  Color restoration coefficient:
  $$C_i(x, y) = \beta \left( \log(\alpha I_i(x, y) + 1) - \log\left(\sum_{j} I_j(x, y) + 1\right) \right)$$
* **Open Source Reference Implementations:**
  * GIMP / GEGL `contrast-retinex` filter (`plug-ins/common/contrast-retinex.c`).

---

### B. Multi-Scale Dyadic Wavelet Decomposition
* **Foundational Papers:**
  * Mallat, S. G. (1989). *A theory for multiresolution signal decomposition: the wavelet representation.* IEEE Transactions on Pattern Analysis and Machine Intelligence, 11(7), 674-693.
  * Starck, J. L., Murtagh, F., & Bijaoui, A. (1998). *Image processing and data analysis: the multiscale approach.* Cambridge University Press.
* **Core Formulation:**
  Exact reversible dyadic pyramid decomposition:
  $$\text{Detail}_k = \text{Scale}_{k-1} - \text{Blur}(\text{Scale}_{k-1}, 2^k)$$
  $$\text{Reconstruction} = \text{Residual} + \sum_{k=1}^{K} \text{Detail}_k$$
* **Open Source Reference Implementations:**
  * GIMP `wavelet-decompose` plug-in by Miroslav Talasek (`plug-ins/common/wavelet-decompose.c`).

---

### C. Asymmetric Tonemapping & Low-Frequency Illumination Masking
* **Foundational Papers:**
  * Reinhard, E., Stark, M., Shirley, P., & Ferwerda, J. (2002). *Photographic tone reproduction for digital images.* ACM Transactions on Graphics (TOG), 21(3), 267-276.
  * Durand, F., & Dorsey, J. (2002). *Fast bilateral filtering for the display of high-dynamic-range images.* ACM Transactions on Graphics (TOG), 21(3), 257-266.
* **Open Source Reference Implementations:**
  * GEGL `shadows-highlights` operation.

---

### D. Thresholded High-Pass Unsharp Masking
* **Foundational Papers:**
  * Polesel, A., Ramponi, G., & Mathews, V. J. (2000). *Image enhancement via adaptive unsharp masking.* IEEE Transactions on Image Processing, 9(3), 505-510.
* **Open Source Reference Implementations:**
  * GEGL `unsharp-mask` operation.

---

## 2. Machine Learning & Neural Micro-Models

### A. Super-Resolution & Fast Restoration
* **Real-ESRGAN General x4v3:**
  * Wang, X., Xie, L., Dong, C., & Shan, Y. (2021). *Real-ESRGAN: Training Real-World Blind Super-Resolution with Pure Synthetic Data.* ICCV Workshops.
  * Qualcomm AI Hub W8A8 optimization: https://huggingface.co/qualcomm/Real-ESRGAN-General-x4v3
* **SPAN / SPAN-F (Swift Parameter-free Attention Network):**
  * Winner of NTIRE 2024 Efficient Super-Resolution Challenge.
  * Repository: https://github.com/hongyuanyu/SPAN
* **SAFMN / SAFMN++ (Spatially-Adaptive Feature Modulation):**
  * Sun, L., Dong, J., Tang, J., & Pan, J. (2023). *Spatially-Adaptive Feature Modulation for Efficient Image Super-Resolution.* ICCV.
  * Repository: https://github.com/sunny2109/SAFMN

---

### B. Multi-Purpose Restoration & Denoising
* **RAMiT (Reciprocal Attention Mixing Transformer):**
  * Choi, J., et al. (2024). *Reciprocal Attention Mixing Transformer for Lightweight Image Restoration.* CVPRW (NTIRE 2024).
  * Repository: https://github.com/rami0205/RAMiT
* **NAFNet (Nonlinear Activation Free Network):**
  * Chen, L., Chu, X., Zhang, X., & Sun, J. (2022). *Simple Baselines for Image Restoration.* ECCV.
  * Repository: https://github.com/megvii-research/NAFNet
* **SCUNet (Swin-Conv UNet):**
  * Zhang, K., Li, Y., Liang, J., Cao, J., & Timofte, R. (2022). *Practical Blind Denoising via Swin-Conv UNet and Data Synthesis.* IJCV.
  * Repository: https://github.com/cszn/SCUNet

---

### C. Atmospheric & Facial Restoration
* **DehazeFormer:**
  * Song, Y., et al. (2023). *Vision Transformers for Single Image Dehazing.* IEEE TIP.
  * Repository: https://github.com/IDKiro/DehazeFormer
* **CodeFormer:**
  * Zhou, S., Chan, K., Li, C., & Loy, C. C. (2022). *Towards Robust Blind Face Restoration with Codebook Lookup Transformer.* NeurIPS.
  * Repository: https://github.com/sczhou/CodeFormer
* **YuNet Face Detection:**
  * OpenCV Zoo: https://github.com/opencv/opencv_zoo/tree/master/models/face_detection_yunet
