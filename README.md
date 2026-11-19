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

<img width="650" height="347" alt="image" src="https://github.com/user-attachments/assets/08cb8ea2-1bd2-44e8-ad04-df6d941c70b9" />


# Sliding Values

1. Hover your mouse to the socket and hold "Middle Mouse" to scroll.

<img width="366" height="161" alt="image" src="https://github.com/user-attachments/assets/8b52d819-642c-4f6e-8254-ff9eade55812" />

2. It uses similar logic of detection and overrides. Additionally you can hold Shift to automatically slide with 1 more decimals for more precision.

# Alternative Trigger on Sliding Values

The Default Sliding value uses "Middle Mouse Button", which is also used for panning in the node tree.

To avoid accidents that you want to pan but you end up sliding values in the node tree.

There's an option in the preference to slide only with the popup

<img width="655" height="181" alt="image" src="https://github.com/user-attachments/assets/d88adc9f-43e3-457a-b7f5-b8c59f0f2ccb" />

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

