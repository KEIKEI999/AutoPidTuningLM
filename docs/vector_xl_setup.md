# Vector XL Smoke Test

The local SDK path discovered in this environment is:

`C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14`

For a one-shot shell session:

```powershell
$env:VECTOR_XL_SDK_DIR='C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14'
$env:PATH='C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14\bin;' + $env:PATH
```

Build the real `vector_xl` variant with VS2017 `msbuild`:

```powershell
& 'C:\Program Files (x86)\Microsoft Visual Studio\2017\WDExpress\MSBuild\15.0\Bin\MSBuild.exe' `
  controller\vs2017\controller.sln /t:Build /p:Configuration=Release /p:Platform=Win32 /p:CanAdapter=vector_xl /m /nologo
```

Run the controller smoke test:

```powershell
scripts\run_vector_xl_smoke.bat
```

The current smoke path opens the Vector XL port, sends one heartbeat frame and one status frame, then closes the port.
If `xlGetApplConfig("AutoTuningLM", channel_index, ...)` is not configured, the adapter falls back to `xlGetDriverConfig()` and selects the requested ordinal channel from the available channels.

Run the plant roundtrip smoke test on the same Vector XL virtual channel:

```powershell
python plant/vector_xl_roundtrip.py --target configs/target_response.yaml --case first_order_nominal --output-dir .tmp_tests/vector_xl_roundtrip
```

This roundtrip opens two Vector XL ports in the same process, injects `setpoint`, `control_output`, and `heartbeat` from the host side, lets the plant simulator respond with `measurement` and `heartbeat`, and stores `waveform.csv` plus `summary.json`.

Run the C controller and Python plant simulator roundtrip:

```powershell
python -m plant.controller_vector_xl_roundtrip --target configs/target_response.yaml --case first_order_nominal --output-dir .tmp_tests/controller_vector_xl_roundtrip
```

This path launches `controller/build/Release/controller.exe`, sends `setpoint` and `measurement` from the plant-side host, receives `control_output`, `status`, and `heartbeat` from the C controller, and writes controller stdout/stderr alongside the waveform.
