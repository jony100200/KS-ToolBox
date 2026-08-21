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
### E. Creative & Aesthetic Image Filters
* **Soft Glow / Orton Bloom Effect:**
  * Orton, M. (1987). *Photographing with the Orton Effect.*
  * Screen-blended Gaussian bloom over highlight thresholds:
    $$\text{Bloom}(x,y) = \text{Blur}(\text{Threshold}(\text{Luma}(I), T), \sigma)$$
    $$I_{\text{glow}} = 1 - (1 - I) \cdot (1 - \text{Bloom})$$
* **Midtone Clarity & Micro-Contrast:**
  * Paris, S., Hasinoff, S. W., & Kautz, J. (2011). *Local Laplacian filters: Edge-aware image processing with a Laplacian pyramid.* ACM Transactions on Graphics (TOG).
  * Bell-curve modulated local frequency boost:
    $$I_{\text{clarity}} = I + (I - \text{Blur}(I, r)) \cdot 4 \cdot L \cdot (1 - L) \cdot \text{amount}$$
* **Radial Lens Vignette:**
  * Smooth cosine falloff from normalized center coordinates:
    $$\text{dist} = \sqrt{\left(\frac{x - c_x}{c_x}\right)^2 + \left(\frac{y - c_y}{c_y}\right)^2}$$
    $$\text{falloff} = \frac{1 - \cos(\text{clamp}(\frac{\text{dist} - r_{\text{inner}}}{r_{\text{outer}} - r_{\text{inner}}}, 0, 1) \cdot \pi)}{2}$$
* **Dual-Tone Split Color Grading:**
  * Independent shadow chromatic bias ($1 - 2L$) vs highlight chromatic bias ($2L - 1$).

---

### F. Adaptive Color Constancy & Local Equalization
* **Contrast-Limited Adaptive Histogram Equalization (CLAHE):**
  * Pizer, S. M., et al. (1987). *Adaptive histogram equalization and its variations.* Computer Vision, Graphics, and Image Processing.
  * Zuiderveld, K. (1994). *Contrast limited adaptive histogram equalization.* Graphics Gems IV, 474-485.
* **Shades of Gray / Minkowski Illuminant Estimation (Auto White-Balance):**
  * Finlayson, G. D., & Trezzi, E. (2004). *Shades of gray and colour constancy.* Color and Imaging Conference.
  * Illuminant chromaticity vector estimated via $p$-norm:
    $$e_p = \left( \frac{1}{N} \sum_{x,y} I_c(x,y)^p \right)^{1/p}$$
* **Selective Adaptive Median Despeckle:**
  * Hwang, H., & Haddad, R. A. (1995). *Adaptive median filters: new algorithms and results.* IEEE Transactions on Image Processing, 4(4), 499-502.
* **Single Image Haze Removal Using Dark Channel Prior (DCP):**
  * He, K., Sun, J., & Tang, X. (2009). *Single image haze removal using dark channel prior.* IEEE Transactions on Pattern Analysis and Machine Intelligence (TPAMI), 33(12), 2341-2353.

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
