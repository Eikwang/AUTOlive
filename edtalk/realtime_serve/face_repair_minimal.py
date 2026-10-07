'''
Minimal Face Repair Script (ONNX Runtime)
Uses GPEN-BFR-256.onnx with onnxruntime-gpu for fast inference.
Usage: python face_repair_minimal.py --indir <input_dir> --outdir <output_dir>
'''
import os
import cv2
import glob
import argparse
import numpy as np
import onnxruntime as ort

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--indir', type=str, required=True, help='input folder with cropped face images')
    parser.add_argument('--outdir', type=str, required=True, help='output folder for repaired images')
    parser.add_argument('--model', type=str, default='weights/facefix256.onnx', help='onnx model path')
    parser.add_argument('--cpu', action='store_true', help='use cpu instead of cuda')
    parser.add_argument('--alpha', type=float, default=0.7, help='repair strength (0=original, 1=full repair)')
    parser.add_argument('--ext', type=str, default='.jpg', help='extension of output')
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    providers = ['CPUExecutionProvider'] if args.cpu else ['CUDAExecutionProvider', 'CPUExecutionProvider']
    sess_options = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(args.model, sess_options=sess_options, providers=providers)
    input_name = sess.get_inputs()[0].name
    print(f'ONNX Runtime provider: {sess.get_providers()[0]}')

    files = sorted(glob.glob(os.path.join(args.indir, '*.*g')))
    for n, file in enumerate(files):
        filename, ext = os.path.splitext(os.path.basename(file))

        img = cv2.imread(file, cv2.IMREAD_COLOR)
        if not isinstance(img, np.ndarray):
            print(filename, 'error')
            continue

        img_t = (img[..., ::-1].astype(np.float32) / 255.0 - 0.5) / 0.5
        img_t = img_t.transpose(2, 0, 1)[np.newaxis, ...]

        ort_out = sess.run(None, {input_name: img_t})[0]

        out = (ort_out[0].transpose(1, 2, 0)[..., ::-1] * 0.5 + 0.5) * 255.0
        img_out = np.clip(out, 0, 255).astype(np.uint8)

        if args.alpha < 1.0:
            img_out = cv2.addWeighted(img_out, args.alpha, img, 1.0 - args.alpha, 0.0)

        cv2.imwrite(f'{args.outdir}/{filename}{args.ext}', img_out)

        if n % 10 == 0:
            print(n, filename)

    print('Done! Repaired images saved to', args.outdir)
