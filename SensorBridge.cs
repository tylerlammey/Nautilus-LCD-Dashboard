using System;
using System.Collections.Generic;
using System.Threading;
using System.IO;
using System.Diagnostics;
using System.Web.Script.Serialization;
using LibreHardwareMonitor.Hardware;

// Read-only CPU/GPU sampling. No controllers, fan curves or RGB are enabled.
class SensorBridge
{
    static void Collect(IHardware hardware, List<object> sensors)
    {
        hardware.Update();
        foreach (ISensor sensor in hardware.Sensors)
        {
            if (sensor.SensorType != SensorType.Temperature && sensor.SensorType != SensorType.Load)
                continue;
            sensors.Add(new {
                hardware = hardware.Name,
                hardwareType = hardware.HardwareType.ToString(),
                name = sensor.Name,
                type = sensor.SensorType.ToString(),
                value = sensor.Value,
                id = sensor.Identifier.ToString()
            });
        }
        foreach (IHardware child in hardware.SubHardware) Collect(child, sensors);
    }

    static int Main(string[] args)
    {
        Computer computer = new Computer { IsCpuEnabled = true, IsGpuEnabled = true };
        JavaScriptSerializer serializer = new JavaScriptSerializer();
        int parentIndex = Array.IndexOf(args, "--parent");
        int parentPid = parentIndex >= 0 ? int.Parse(args[parentIndex + 1]) : 0;
        Mutex helperMutex = null;
        if (parentPid != 0)
        {
            bool created;
            helperMutex = new Mutex(false, "Local\\NautilusSensorBridge" + parentPid, out created);
            if (!created) { helperMutex.Dispose(); return 0; }
        }
        try
        {
            computer.Open();
            do
            {
                if (parentPid != 0)
                {
                    try { if (Process.GetProcessById(parentPid).HasExited) break; }
                    catch (ArgumentException) { break; }
                }
                List<object> sensors = new List<object>();
                foreach (IHardware hardware in computer.Hardware) Collect(hardware, sensors);
                string message = serializer.Serialize(new { sensors = sensors, parent_pid = parentPid });
                int outputIndex = Array.IndexOf(args, "--output");
                if (outputIndex >= 0 && outputIndex + 1 < args.Length)
                {
                    string destination = args[outputIndex + 1];
                    File.WriteAllText(destination + ".tmp", message);
                    try
                    {
                        if (File.Exists(destination)) File.Replace(destination + ".tmp", destination, null);
                        else File.Move(destination + ".tmp", destination);
                    }
                    catch (IOException) { } // A reader can briefly hold the previous sample open.
                }
                else
                    Console.WriteLine(message);
                if (Array.IndexOf(args, "--once") >= 0) break;
                Thread.Sleep(1000);
            } while (true);
            return 0;
        }
        catch (Exception error)
        {
            Console.WriteLine(serializer.Serialize(new { error = error.Message }));
            return 1;
        }
        finally
        {
            computer.Close();
            if (helperMutex != null) helperMutex.Dispose();
        }
    }
}
