# Pixel Art & Dithering References & Formulations

Clean-room mathematical formulations and academic citations for the deterministic pixel art algorithms implemented in `tools/pixel_art`:

---

## 1. Ordered Bayer Matrix Dithering

* **Citation:**
  * Bayer, B. E. (1973). *An optimum method for two-level rendition of continuous-tone pictures.* IEEE International Conference on Communications, 1, 26-33.
* **Formulation:**
  $$M_2 = \frac{1}{4} \begin{bmatrix} 0 & 2 \\ 3 & 1 \end{bmatrix}, \quad M_{2n} = \frac{1}{4n^2} \begin{bmatrix} 4 M_n & 4 M_n + 2 U_n \\ 4 M_n + 3 U_n & 4 M_n + 1 U_n \end{bmatrix}$$
  $$I_{\text{modulated}}(x, y) = I(x, y) + \left( M_N(x \bmod N, y \bmod N) - 0.5 \right) \cdot \text{spread} \cdot \text{strength}$$

---

## 2. Floyd-Steinberg Error Diffusion

* **Citation:**
  * Floyd, R. W., & Steinberg, L. (1976). *An adaptive algorithm for spatial grey scale.* Proceedings of the Society for Information Display, 17, 75-77.
* **Diffusion Kernel:**
  $$\begin{bmatrix} & * & \frac{7}{16} \\ \frac{3}{16} & \frac{5}{16} & \frac{1}{16} \end{bmatrix}$$

---

## 3. Atkinson Dithering

* **Citation:**
  * Atkinson, B. (1984). *Atkinson Dithering Algorithm.* Apple Computer, MacPaint 1.0.
* **Diffusion Kernel:**
  $$\begin{bmatrix} & & * & \frac{1}{8} & \frac{1}{8} \\ & \frac{1}{8} & \frac{1}{8} & \frac{1}{8} & \\ & & \frac{1}{8} & & \end{bmatrix}$$

---

## 4. Perceptual CIELAB Color Distance

* **Citation:**
  * Robertson, A. R. (1977). *The CIE 1976 color-difference formulae.* Color Research & Application, 2(1), 7-11.
* **Euclidean Distance in CIELAB Space:**
  $$\Delta E_{76}^* = \sqrt{(L_1^* - L_2^*)^2 + (a_1^* - a_2^*)^2 + (b_1^* - b_2^*)^2}$$
