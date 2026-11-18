# Value-Precision
A Blender add-on that allows you to input or modify values more precisely. Made with Claude.

# Type Values
1. Hover your mouse to the socket and hit shortcut, by default as "`" (the Backtick key under the Esc and on the left of "1")

<img width="384" height="174" alt="image" src="https://github.com/user-attachments/assets/5a97564c-b643-49a7-8520-33425dcb0147" />

2. A window pop up. You can just type any values like 5. By default it runs on Division, and you can choose the exponent or precision.

3. You are able the preview the result you are going to enter. Space or LMB in other space will apply the value, or RMB will cancel the operation.

# Automatic Detection and Overrides.

1.if the selected Socket has a value of 0.005, it has 3 decimals, so the input value will by default considered in the precision of 10^3.

<img width="347" height="163" alt="image" src="https://github.com/user-attachments/assets/fab150df-e525-4e49-aff7-a2983b9ade22" />

2. you can utilize the shortcut Z to decrease exponent or X to increase exponent (or precision) for the final values.

3. Shortcut "C" will be used to switch operations between division and multiply.

4. Shortcut "V" will be used to decide if the final values should be positive or negative.

<img width="392" height="172" alt="image" src="https://github.com/user-attachments/assets/8802bf1e-c800-4b6f-99bc-c9a50a8aecc4" />

All shortcuts are modifiable in the preferences

<img width="650" height="347" alt="image" src="https://github.com/user-attachments/assets/08cb8ea2-1bd2-44e8-ad04-df6d941c70b9" />


# Sliding Values

1. Hover your mouse to the socket and hold "Middle Mouse" to scroll.

<img width="366" height="161" alt="image" src="https://github.com/user-attachments/assets/8b52d819-642c-4f6e-8254-ff9eade55812" />

2. It uses similar logic of detection and overrides. Additionally you can hold Shift to automatically slide with 1 more decimals.
