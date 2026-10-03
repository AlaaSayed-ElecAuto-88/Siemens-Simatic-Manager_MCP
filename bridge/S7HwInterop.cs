// Early-bound access to STEP 7's IS7Module6 hardware interface (S7HCOM_X.DLL).
//
// The scripting (IDispatch) side of a module object only exposes an older interface, so
// members such as IPAddress, UpdateData and GetOnlineDiagBuffer are unreachable from
// PowerShell. This declares the interface's vtable so they can be called directly.
// The declaration is generated from the type library: members we call have real
// signatures, every other vtable slot is a placeholder, and the ORDER MUST NOT CHANGE.
// s7bridge.ps1 compiles this file with Add-Type.

using System;
using System.Runtime.InteropServices;

namespace S7Hw {
    [ComImport, Guid("E5501B37-117F-419C-80AF-79F073107988"), InterfaceType(ComInterfaceType.InterfaceIsDual)]
    public interface IS7Module6 {
        void _slot0();
        void _slot1();
        void _slot2();
        void _slot3();
        void _slot4();
        void _slot5();
        void _slot6();
        void _slot7();
        void _slot8();
        void _slot9();
        void _slot10();
        void _slot11();
        void _slot12();
        void _slot13();
        void _slot14();
        void _slot15();
        void _slot16();
        int RegisterAddresses();
        void _slot18();
        void _slot19();
        void _slot20();
        void _slot21();
        void _slot22();
        void _slot23();
        void _slot24();
        void _slot25();
        void _slot26();
        void _slot27();
        void _slot28();
        void _slot29();
        void _slot30();
        void _slot31();
        void _slot32();
        void _slot33();
        void _slot34();
        [return: MarshalAs(UnmanagedType.BStr)] string GetIPAddress();
        void SetIPAddress([MarshalAs(UnmanagedType.BStr)] string value);
        void _slot37();
        void _slot38();
        [return: MarshalAs(UnmanagedType.BStr)] string GetSubnetMask();
        void SetSubnetMask([MarshalAs(UnmanagedType.BStr)] string value);
        int GetRouterActive();
        void SetRouterActive(int value);
        [return: MarshalAs(UnmanagedType.BStr)] string GetRouterAddress();
        void SetRouterAddress([MarshalAs(UnmanagedType.BStr)] string value);
        void _slot45();
        void _slot46();
        void _slot47();
        void _slot48();
        void _slot49();
        void _slot50();
        void _slot51();
        void _slot52();
        void _slot53();
        void _slot54();
        void _slot55();
        void _slot56();
        void _slot57();
        void _slot58();
        void _slot59();
        void _slot60();
        void _slot61();
        int GetOnlineDiagBuffer([MarshalAs(UnmanagedType.BStr)] string filePath);
        void _slot63();
        void UpdateData();
        void _slot65();
        void _slot66();
        void _slot67();
        void _slot68();
        void _slot69();
        void _slot70();
        void _slot71();
        void _slot72();
        void _slot73();
        void _slot74();
        void _slot75();
        void _slot76();
        void _slot77();
        void _slot78();
        int GetMPIAddress();
        void SetMPIAddress(int value);
        void _slot81();
        void _slot82();
        void _slot83();
        void _slot84();
        void _slot85();
        void _slot86();
        void _slot87();
        void _slot88();
        void _slot89();
        void ExportCPUMessagesToCSV([MarshalAs(UnmanagedType.BStr)] string fileName);
        void GetAddressRangeInfo([MarshalAs(UnmanagedType.BStr)] out string inRange, [MarshalAs(UnmanagedType.BStr)] out string outRange, [MarshalAs(UnmanagedType.BStr)] out string diagAddr);
        void GetAddressDetails([MarshalAs(UnmanagedType.BStr)] out string details);
        void SdbCompareOnlineOffline(ref int retCode);
    }

    public static class Module {
        static IS7Module6 Of(object module) { return (IS7Module6)module; }

        public static string GetIP(object m) { return Of(m).GetIPAddress(); }
        public static void SetIP(object m, string value) { Of(m).SetIPAddress(value); }
        public static string GetSubnetMask(object m) { return Of(m).GetSubnetMask(); }
        public static void SetSubnetMask(object m, string value) { Of(m).SetSubnetMask(value); }
        public static string GetRouter(object m) { return Of(m).GetRouterActive() != 0 ? Of(m).GetRouterAddress() : ""; }
        public static void SetRouter(object m, string value) {
            Of(m).SetRouterActive(string.IsNullOrEmpty(value) ? 0 : 1);
            if (!string.IsNullOrEmpty(value)) Of(m).SetRouterAddress(value);
        }
        public static int GetMPIAddress(object m) { return Of(m).GetMPIAddress(); }
        public static void SetMPIAddress(object m, int value) { Of(m).SetMPIAddress(value); }

        // Makes changed I/O addresses permanent; without UpdateData STEP 7 forgets them.
        public static void CommitAddresses(object m) { Of(m).RegisterAddresses(); Of(m).UpdateData(); }

        // Writes the CPU's diagnostic buffer, with STEP 7's event texts, to a text file.
        public static int ExportDiagnosticBuffer(object cpu, string path) { return Of(cpu).GetOnlineDiagBuffer(path); }
    }
}
