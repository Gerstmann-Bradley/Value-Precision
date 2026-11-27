# Value-Precision
A Blender add-on that allows you to input or modify values more precisely. Made with Claude.

It works everywhere in Blender. For simplicity, most demonstrations use Geometry Nodes as an example.

# Type Values
1. Hover your mouse to the socket and hit shortcut, by default as "`"

   (the Backtick key under the Esc and on the left of "1")

<img width="384" height="174" alt="image" src="https://github.com/user-attachments/assets/5a97564c-b643-49a7-8520-33425dcb0147" />

2. A window pop up. You can just type any values like 5. By default it runs on Division, and you can choose the exponent or precision.

3. You are able the preview the result you are going to enter. Space or LMB in other space will apply the value, or RMB will cancel the operation.

# Automatic Detection and Overrides.

1.if the selected Socket has a value of 0.005, it has 3 decimals, so the input value will by default considered in the precision of 10^3.

<img width="347" height="163" alt="image" src="https://github.com/user-attachments/assets/fab150df-e525-4e49-aff7-a2983b9ade22" />

2. you can utilize the shortcut Z to decrease exponent or X to increase exponent (or precision) for the final values.

3. Besides Auto-Detection, shortcut "C" will be used to switch operations between division and multiply.

4. Besides Auto-Detection on Sign, Shortcut "V" will be used to decide if the final values should be positive or negative.

<img width="392" height="172" alt="image" src="https://github.com/user-attachments/assets/8802bf1e-c800-4b6f-99bc-c9a50a8aecc4" />

All shortcuts are modifiable in the preferences

<img width="663" height="381" alt="image" src="https://github.com/user-attachments/assets/0f61c65d-6064-49b8-a035-fde4b6f8fa12" />


# Sliding Values

1. Hover your mouse to the socket and hold "Middle Mouse" to scroll.

<img width="366" height="161" alt="image" src="https://github.com/user-attachments/assets/8b52d819-642c-4f6e-8254-ff9eade55812" />

2. It uses similar logic of detection and overrides. Additionally you can hold Shift to automatically slide with 1 more decimals for more precision.

# Alternative Trigger on Sliding Values

The Default Sliding value uses "Middle Mouse Button", which is also used for panning in the node tree.

To avoid accidents that you want to pan but you end up sliding values in the node tree.

There's an option in the preference to slide only with the popup

<img width="824" height="222" alt="image" src="https://github.com/user-attachments/assets/92aff478-3b7f-4f18-a3f0-2c1a58bca918" />

So the process becomes:

1. You hit default "`" (backtick) key to open the popup
2. then hold middle mouse scroll to switch the mode to the sliding values.

<img width="521" height="174" alt="image" src="https://github.com/user-attachments/assets/bb69e803-ae12-4e01-a1a9-0d77fbbc181a" />

<img width="370" height="163" alt="image" src="https://github.com/user-attachments/assets/665ea279-36ea-44af-8fc2-d7adb1a954e8" />

This implementation also opens a different workflow.
1. if you already typed a value and start middle mouse scroll.

<img width="354" height="165" alt="image" src="https://github.com/user-attachments/assets/b1adbbf4-843e-4498-af42-7ff0522ebd67" />

2. it will apply the value first and then start scrolling at the same precision for fine tuning.

# All Axes Support for Vectors
1. By default, when the mouse hovers on vector socket. Whether you Type Value or Slide value, it work on the axis you selected.

2. However, there's an additional "All Axes" option, whether you click it, or use Shortcut A, it will apply the changes to all Shortcuts.

<img width="398" height="202" alt="image" src="https://github.com/user-attachments/assets/02a7ba7d-b1b0-4895-8fcf-7322a1685364" />

A. Typing Values will apply the same values to All Axes

B. Sliding Values will slide each axis differently but at the same precision. For example if you hover on axis X to start with:

```
X: 0.05 -> X: 0.06
Y: 1.00 -> Y: 1.01
```

# Ajusting Existing Values

0. By Default, Adjust Existing Value Option is turned on in Preference.

<img width="673" height="167" alt="image" src="https://github.com/user-attachments/assets/db5341ea-7ab8-4d05-9119-21c41e65a06b" />

1. When you hover the socket and open up the panel, Auto-Detection will instruct you its current decimals.

If you don't make any changes, whether clicking outside or RMB canceling, the value will stay as it is.

<img width="494" height="162" alt="image" src="https://github.com/user-attachments/assets/9e2acb2b-cd27-474c-9f81-b18cbb9a168d" />

2. However, if you change the decimal position without entering any new values,

here, decreasing the decimal places by 1 is equivalent to multiplying the value by 10.

<img width="464" height="160" alt="image" src="https://github.com/user-attachments/assets/e2cb0b45-8ae3-49c3-b7af-a97771562fd9" />

3. When you apply the change, the existing values are adjusted accordingly, turning 0.59 into 5.9.

<img width="369" height="66" alt="image" src="https://github.com/user-attachments/assets/3db18bca-ed5c-4984-b45d-cc67002133db" />
