"""Build tiny synthetic models, without downloading biometric model weights."""
from pathlib import Path
import sys


def generate(destination):
    import flatbuffers
    import onnx
    from onnx import TensorProto, helper
    import tflite as t

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    graph = helper.make_graph([helper.make_node('Relu', ['input'], ['output'])], 'native-smoke',
        [helper.make_tensor_value_info('input', TensorProto.FLOAT, [1, 1, 2, 2])],
        [helper.make_tensor_value_info('output', TensorProto.FLOAT, [1, 1, 2, 2])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)], ir_version=8)
    onnx.checker.check_model(model)
    onnx.save(model, destination / 'relu.onnx')

    builder = flatbuffers.Builder(1024)
    def ints(values):
        builder.StartVector(4, len(values), 4)
        for value in reversed(values):
            builder.PrependInt32(value)
        return builder.EndVector()
    def offsets(values):
        builder.StartVector(4, len(values), 4)
        for value in reversed(values):
            builder.PrependUOffsetTRelative(value)
        return builder.EndVector()
    tensors = []
    for name in ('input', 'output'):
        label = builder.CreateString(name)
        shape = ints([1, 1, 2, 2])
        t.TensorStart(builder)
        t.TensorAddShape(builder, shape)
        t.TensorAddType(builder, t.TensorType.FLOAT32)
        t.TensorAddBuffer(builder, 0)
        t.TensorAddName(builder, label)
        tensors.append(t.TensorEnd(builder))
    inputs, outputs = ints([0]), ints([1])
    t.OperatorStart(builder)
    t.OperatorAddOpcodeIndex(builder, 0)
    t.OperatorAddInputs(builder, inputs)
    t.OperatorAddOutputs(builder, outputs)
    operator = t.OperatorEnd(builder)
    tensors_vector, operators_vector = offsets(tensors), offsets([operator])
    t.SubGraphStart(builder)
    t.SubGraphAddTensors(builder, tensors_vector)
    t.SubGraphAddInputs(builder, inputs)
    t.SubGraphAddOutputs(builder, outputs)
    t.SubGraphAddOperators(builder, operators_vector)
    graph_offset = t.SubGraphEnd(builder)
    t.OperatorCodeStart(builder)
    t.OperatorCodeAddBuiltinCode(builder, t.BuiltinOperator.RELU)
    t.OperatorCodeAddDeprecatedBuiltinCode(builder, t.BuiltinOperator.RELU)
    t.OperatorCodeAddVersion(builder, 1)
    code = t.OperatorCodeEnd(builder)
    t.BufferStart(builder)
    buffer = t.BufferEnd(builder)
    graphs, codes, buffers = offsets([graph_offset]), offsets([code]), offsets([buffer])
    t.ModelStart(builder)
    t.ModelAddVersion(builder, 3)
    t.ModelAddSubgraphs(builder, graphs)
    t.ModelAddOperatorCodes(builder, codes)
    t.ModelAddBuffers(builder, buffers)
    model = t.ModelEnd(builder)
    builder.Finish(model, file_identifier=b'TFL3')
    (destination / 'relu.tflite').write_bytes(builder.Output())


if __name__ == '__main__':
    generate(sys.argv[1])
