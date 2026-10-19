"""Execute synthetic ReLU fixtures through every native inference backend."""
from pathlib import Path


def check_inference(fixtures):
    import cv2
    import numpy as np
    import onnxruntime as ort
    from ai_edge_litert.interpreter import Interpreter

    fixtures = Path(fixtures)
    value = np.array([-2, -1, 1, 2], dtype=np.float32).reshape(1, 1, 2, 2)
    expected = np.maximum(value, 0)
    net = cv2.dnn.readNetFromONNX(str(fixtures / 'relu.onnx'))
    net.setInput(value)
    np.testing.assert_allclose(net.forward(), expected)
    session = ort.InferenceSession(str(fixtures / 'relu.onnx'), providers=['CPUExecutionProvider'])
    np.testing.assert_allclose(session.run(None, {'input': value})[0], expected)
    interpreter = Interpreter(model_path=str(fixtures / 'relu.tflite'), num_threads=1)
    interpreter.allocate_tensors()
    interpreter.set_tensor(interpreter.get_input_details()[0]['index'], value)
    interpreter.invoke()
    np.testing.assert_allclose(interpreter.get_tensor(interpreter.get_output_details()[0]['index']), expected)
